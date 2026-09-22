"""Closed records for immutable storage; references never contain locations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Literal

from agent_evals.core import EvaluationError, MAX_BYTES, _HASH, _ID, _pairs
from .campaign import _typed, _schema_for


AccessClass = Literal["private", "public"]
RecordSchema = Literal[
    "codex.agent-evals-evidence/v1", "codex.agent-evals-execution-import/v1",
    "codex.agent-evals-result-set/v1", "codex.agent-evals-public-summary/v1",
    "codex.agent-evals-public-manifest/v1", "codex.agent-evals-analysis-spec/v1",
    "codex.agent-evals-blinded-packet/v1", "codex.agent-evals-neutral-mapping/v1",
    "codex.agent-evals-analysis-result/v1", "codex.agent-evals-comparison/v1",
]
Classification = Literal["usable", "incomplete", "unavailable", "invalid"]
Missingness = Literal["present", "missing"]


@dataclass(frozen=True)
class ContentRef:
    digest: str
    byte_size: int
    media_type: Literal["application/json", "text/plain"]
    record_schema: RecordSchema
    access_class: AccessClass


@dataclass(frozen=True)
class AdmittedEvidence:
    response: str
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResultImport:
    campaign_sha256: str
    trial_id: str
    requested_profile_sha256: str
    observed_profile_sha256: str | None
    completion: Literal["complete", "partial", "unavailable"]
    evidence_ref: ContentRef | None


@dataclass(frozen=True)
class ResultSetEntry:
    trial_id: str
    execution_ref: ContentRef | None
    evidence_ref: ContentRef | None
    classification: Classification
    missingness: Missingness
    evidence_verification: Literal["verified", "missing"]


@dataclass(frozen=True)
class SealedResultSet:
    campaign_sha256: str
    revision: int
    predecessor: ContentRef | None
    entries: tuple[ResultSetEntry, ...]


@dataclass(frozen=True)
class PublicSummary:
    classification: Classification
    missingness: Missingness


@dataclass(frozen=True)
class PublicContentRef:
    digest: str
    byte_size: int
    media_type: Literal["application/json"]
    record_schema: Literal["codex.agent-evals-public-summary/v1"]
    access_class: Literal["public"]


@dataclass(frozen=True)
class PublishedResultManifest:
    result_set_sha256: str
    summaries: tuple[PublicContentRef, ...]
    redaction_revision: Literal["closed-projection-v1"]
    access_class: Literal["public"]
    retention_policy_sha256: str


RECORD_TYPES = {
    AdmittedEvidence: "codex.agent-evals-evidence/v1",
    ExecutionResultImport: "codex.agent-evals-execution-import/v1",
    SealedResultSet: "codex.agent-evals-result-set/v1",
    PublicSummary: "codex.agent-evals-public-summary/v1",
    PublishedResultManifest: "codex.agent-evals-public-manifest/v1",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def hash_field(value: str) -> None:
    require(type(value) is str and bool(_HASH.fullmatch(value)), "invalid content digest")


def validate_content_ref(ref: ContentRef) -> None:
    _typed(ref, ContentRef)
    hash_field(ref.digest)
    require(0 <= ref.byte_size <= MAX_BYTES, "content byte bound")


def _records() -> dict:
    from .analysis import ANALYSIS_TYPES
    return RECORD_TYPES | ANALYSIS_TYPES


def validate_publication_record(value: object) -> None:
    require(type(value) in _records(), "unknown publication record")
    _typed(value, type(value))
    if type(value) is ExecutionResultImport:
        hash_field(value.campaign_sha256)
        hash_field(value.requested_profile_sha256)
        if value.observed_profile_sha256 is not None:
            hash_field(value.observed_profile_sha256)
        require(bool(_ID.fullmatch(value.trial_id)), "invalid trial identity")
        if value.evidence_ref is not None:
            validate_content_ref(value.evidence_ref)
            require(value.evidence_ref.record_schema == RECORD_TYPES[AdmittedEvidence], "wrong evidence schema")
    elif type(value) is SealedResultSet:
        hash_field(value.campaign_sha256)
        require(value.revision >= 1, "invalid revision")
        require((value.revision == 1) == (value.predecessor is None), "predecessor required")
        if value.predecessor is not None:
            validate_content_ref(value.predecessor)
            require(value.predecessor.record_schema == RECORD_TYPES[SealedResultSet], "wrong predecessor schema")
        ids = tuple(entry.trial_id for entry in value.entries)
        require(bool(ids) and ids == tuple(sorted(set(ids))), "duplicate or noncanonical trials")
        for entry in value.entries:
            require(bool(_ID.fullmatch(entry.trial_id)), "invalid trial identity")
            for ref in (entry.execution_ref, entry.evidence_ref):
                if ref is not None:
                    validate_content_ref(ref)
            require((entry.evidence_ref is None) == (entry.missingness == "missing"), "missingness mismatch")
            require((entry.evidence_ref is None) == (entry.evidence_verification == "missing"), "verification mismatch")
            require(entry.classification != "usable" or entry.evidence_ref is not None, "usable evidence missing")
    elif type(value) is PublishedResultManifest:
        hash_field(value.result_set_sha256)
        hash_field(value.retention_policy_sha256)
        require(bool(value.summaries), "empty public manifest")
        for ref in value.summaries:
            hash_field(ref.digest)
            require(0 <= ref.byte_size <= MAX_BYTES, "content byte bound")
    else:
        from .analysis import validate_analysis_record
        validate_analysis_record(value)


def publication_encode(value: object) -> str:
    validate_publication_record(value)
    text = json.dumps({"schema": _records()[type(value)], "kind": type(value).__name__, "data": asdict(value)}, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    require(len(text.encode("utf-8")) <= MAX_BYTES, "record byte bound")
    return text


def publication_decode(text: str) -> object:
    try:
        require(type(text) is str and len(text.encode("utf-8")) <= MAX_BYTES, "record byte bound")
        raw = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("nonfinite JSON")))
        require(type(raw) is dict and set(raw) == {"schema", "kind", "data"}, "unknown envelope")
        choices = {kind.__name__: (kind, schema) for kind, schema in _records().items()}
        require(type(raw["kind"]) is str and raw["kind"] in choices, "unknown record kind")
        kind, schema = choices[raw["kind"]]
        require(raw["schema"] == schema, "wrong record schema")
        record = _typed(raw["data"], kind, decode=True)
        validate_publication_record(record)
        return record
    except EvaluationError:
        raise
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise EvaluationError("malformed publication record") from exc


def publication_json_schema(record_type: type) -> dict:
    require(record_type in _records(), "unknown schema type")
    schema = _schema_for(record_type, _records()[record_type])
    for definition in schema["$defs"].values():
        for name, prop in definition.get("properties", {}).items():
            if name == "digest" or name.endswith("sha256"):
                if prop.get("type") == "string":
                    prop.update(minLength=64, maxLength=64, pattern="^[0-9a-f]{64}$")
            if name == "byte_size":
                prop.update(minimum=0, maximum=MAX_BYTES)
    return schema

"""Portable, deterministic records and grading for agent evaluations.

The v1 wire schema is generated from the closed dataclasses below. Semantic
checks additionally bind content and public-safe provenance. This module does
not create workspaces, execute agents, contact providers, or qualify runtimes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
import json
import math
from pathlib import PurePosixPath
import re
from types import UnionType
from typing import Callable, Literal, get_args, get_origin, get_type_hints


SCHEMA = "codex.skill-evaluation/v1"
MAX_BYTES = 1024 * 1024
MAX_TEXT = 32768
MAX_ITEMS = 64
DIMENSIONS = ("quality", "process", "style", "reliability", "efficiency")
ARMS = ("baseline", "skill", "do_nothing")
Arm = Literal["baseline", "skill", "do_nothing"]
Mode = Literal["fixture", "calibration", "serving"]
Status = Literal["complete", "unavailable", "invalid", "partial", "inconclusive"]
Dimension = Literal["quality", "process", "style", "reliability", "efficiency"]
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SECRET = re.compile(
    r"(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+\S{12,}"
)


class EvaluationError(ValueError):
    """A sanitized contract error; never include input payloads in diagnostics."""


@dataclass(frozen=True)
class Provenance:
    kind: Literal["fixture", "public_organic", "runtime", "deterministic", "judge", "human", "control"]
    source_id: str
    revision: str
    public_safe_reviewed: bool


@dataclass(frozen=True)
class RuntimeTuple:
    provider: str | None
    model: str | None
    model_revision: str | None
    effort: str | None
    runtime: str | None
    runtime_revision: str | None


@dataclass(frozen=True)
class Asset:
    path: str
    text: str


@dataclass(frozen=True)
class Case:
    case_id: str
    version: int
    provenance: Provenance
    trigger: Literal["explicit", "implicit", "contextual", "near_miss", "negative_control"]
    skill_name: str
    prompt: str
    context: str
    assets: tuple[Asset, ...]
    human_review_required: bool
    # Existing assets are always captured; only these additional paths may enter evidence.
    output_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Variant:
    variant_id: str
    version: int
    provenance: Provenance
    skill_name: str
    skill_text: str


@dataclass(frozen=True)
class Metrics:
    # None is missing evidence; zero is a measured or structural zero.
    wall_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: int | None


@dataclass(frozen=True)
class Observation:
    provenance: Provenance
    observed_runtime: RuntimeTuple | None
    runtime_evidence_id: str | None
    status: Literal["complete", "partial", "unavailable"]
    response: str
    events: tuple[str, ...] | None
    metrics: Metrics


@dataclass(frozen=True)
class Evidence:
    observation: Observation
    assets: tuple[Asset, ...]


@dataclass(frozen=True)
class Run:
    run_id: str
    case_sha256: str
    variant_sha256: str
    input_sha256: str
    arm: Arm
    mode: Mode
    intended_runtime: RuntimeTuple
    status: Status
    reason: Literal["observed", "no_adapter", "adapter_error", "unsafe_evidence", "runtime_unverified", "tuple_mismatch", "no_op"]
    evidence: Evidence | None
    evidence_sha256: str | None


@dataclass(frozen=True)
class Check:
    check_id: str
    dimension: Dimension
    operation: Literal["file_equals", "response_equals", "response_contains", "response_max_chars", "event_present", "event_absent", "skill_activation", "unchanged", "metric_at_most", "completed"]
    target: str
    expected: str | float | int | bool


@dataclass(frozen=True)
class Rubric:
    rubric_id: str
    version: int
    provenance: Provenance
    case_sha256: str
    checks: tuple[Check, ...]
    judge_required: bool
    judge_instructions: str


@dataclass(frozen=True)
class Measure:
    dimension: Dimension
    value: float | None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    dimension: Dimension
    passed: bool | None


@dataclass(frozen=True)
class JudgePacket:
    evidence_sha256: str
    rubric_sha256: str
    assets: tuple[Asset, ...]
    response: str
    instructions: str


@dataclass(frozen=True)
class Assessment:
    provenance: Provenance
    packet_sha256: str
    intended_runtime: RuntimeTuple
    observed_runtime: RuntimeTuple | None
    runtime_evidence_id: str | None
    status: Literal["complete", "unavailable", "inconclusive"]
    measures: tuple[Measure, ...]


@dataclass(frozen=True)
class HumanReview:
    provenance: Provenance
    evidence_sha256: str
    rubric_sha256: str
    decision: Literal["approved", "rejected", "pending"]


@dataclass(frozen=True)
class Score:
    run_sha256: str
    rubric_sha256: str
    evidence_sha256: str | None
    grader: Provenance
    status: Status
    verdict: Literal["passed", "failed", "not_scored"]
    checks: tuple[CheckResult, ...]
    measures: tuple[Measure, ...]
    assessment: Assessment | None
    human_review: HumanReview | None


RECORDS = {c.__name__: c for c in (Case, Variant, Rubric, Run, Score, Assessment, HumanReview)}


def _fail(condition: bool, reason: str) -> None:
    if not condition:
        raise EvaluationError(reason)


def _text(value: str) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise EvaluationError("invalid Unicode text") from exc
    _fail(size <= MAX_TEXT, "text exceeds bound")
    _fail(not any(ord(c) < 32 and c not in "\n\r\t" or 127 <= ord(c) <= 159 for c in value), "control character")
    _fail(not _SECRET.search(value), "secret-like content")


def _path(value: str) -> None:
    path = PurePosixPath(value)
    _fail(bool(value) and len(value) <= 240 and path.as_posix() == value and not path.is_absolute(), "unsafe asset path")
    _fail(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", p) and not p.startswith(".") for p in path.parts), "unsafe asset path")
    _fail(len(path.parts) <= 8, "asset path depth")


def _assets(values: tuple[Asset, ...]) -> None:
    names = [a.path.casefold() for a in values]
    _fail(len(names) == len(set(names)), "duplicate asset")
    _fail(not any(a != b and b.startswith(a + "/") for a in names for b in names), "overlapping assets")
    _fail(sum(len(json.dumps(asdict(a), ensure_ascii=True)) for a in values) <= MAX_BYTES // 2, "asset aggregate bound")


def _semantic(value: object) -> None:
    if isinstance(value, Provenance):
        _fail(bool(_ID.fullmatch(value.source_id)) and bool(_ID.fullmatch(value.revision)), "invalid provenance identifier")
        _fail(value.public_safe_reviewed, "public-safe admission required")
    if isinstance(value, RuntimeTuple):
        _fail(all(v is None or bool(_ID.fullmatch(v)) for v in asdict(value).values()), "invalid runtime tuple")
    if isinstance(value, Asset):
        _path(value.path)
    if isinstance(value, (Case, Variant, Rubric)):
        _fail(value.version > 0, "invalid version")
        name = value.case_id if isinstance(value, Case) else value.variant_id if isinstance(value, Variant) else value.rubric_id
        _fail(bool(_ID.fullmatch(name)), "invalid record identifier")
    if isinstance(value, (Case, Variant)):
        _fail(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value.skill_name)), "invalid skill name")
        _fail(value.provenance.kind in ("fixture", "public_organic"), "invalid case or variant provenance")
    if isinstance(value, Case):
        _fail(bool(value.prompt.strip()), "empty task")
        _assets(value.assets)
        declared = value.assets + tuple(Asset(path, "") for path in value.output_paths)
        _fail(len(declared) <= MAX_ITEMS, "capture path count")
        for path in value.output_paths:
            _path(path)
        _assets(declared)
        _fail(value.provenance.kind != "public_organic" or value.human_review_required, "organic cases require human gate")
    if isinstance(value, Variant):
        _fail(bool(value.skill_text.strip()), "empty skill")
    if isinstance(value, Metrics):
        _fail(all(v is None or v >= 0 for v in asdict(value).values()), "negative metric")
    if isinstance(value, Observation):
        _fail(value.provenance.kind in ("fixture", "runtime", "control"), "invalid observation provenance")
        _fail(value.events is None or all(bool(_ID.fullmatch(e)) for e in value.events), "invalid event")
        _fail(value.runtime_evidence_id is None or bool(_ID.fullmatch(value.runtime_evidence_id)), "invalid runtime evidence identifier")
    if isinstance(value, Evidence):
        _assets(value.assets)
    if isinstance(value, Run):
        _fail(bool(_ID.fullmatch(value.run_id)), "invalid run identifier")
        for h in (value.case_sha256, value.variant_sha256, value.input_sha256):
            _fail(bool(_HASH.fullmatch(h)), "invalid digest")
        _fail((value.evidence is None) == (value.evidence_sha256 is None), "missing evidence binding")
        if value.evidence is not None:
            _fail(digest(value.evidence) == value.evidence_sha256, "evidence digest mismatch")
        _fail(value.status not in ("complete", "inconclusive") or value.evidence is not None, "missing run evidence")
        if value.evidence is None:
            _fail((value.status, value.reason) in (("unavailable", "no_adapter"), ("partial", "adapter_error"), ("invalid", "unsafe_evidence")), "invalid absent-evidence outcome")
    if isinstance(value, Check):
        _fail(bool(_ID.fullmatch(value.check_id)), "invalid check identifier")
        if value.operation == "file_equals":
            _path(value.target)
            _fail(type(value.expected) is str, "file expectation must be text")
        elif value.operation in ("response_equals", "response_contains"):
            _fail(value.target == "" and type(value.expected) is str, "invalid response check")
            _fail(value.operation != "response_contains" or bool(value.expected), "empty response substring")
        elif value.operation in ("event_present", "event_absent"):
            _fail(bool(_ID.fullmatch(value.target)) and value.expected is True, "invalid event check")
        elif value.operation == "skill_activation":
            _fail(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value.target)) and type(value.expected) is bool, "invalid activation check")
        elif value.operation in ("unchanged", "completed"):
            _fail(value.target == "" and value.expected is True, "invalid boolean check")
        else:
            _fail(type(value.expected) in (float, int) and value.expected >= 0, "invalid metric threshold")
            _fail(value.target in ("wall_ms", "input_tokens", "output_tokens", "tool_calls") if value.operation == "metric_at_most" else value.target == "", "invalid metric target")
    if isinstance(value, Rubric):
        _fail(bool(_HASH.fullmatch(value.case_sha256)), "invalid rubric binding")
        _fail(value.provenance.kind in ("fixture", "human"), "invalid rubric provenance")
        _fail(bool(value.checks) and len({c.check_id for c in value.checks}) == len(value.checks), "empty or duplicate checks")
        _fail(not value.judge_required or bool(value.judge_instructions.strip()), "missing judge instructions")
    if isinstance(value, Measure):
        _fail(value.value is None or 0 <= value.value <= 1, "measure outside unit interval")
    if isinstance(value, Assessment):
        _fail(value.provenance.kind == "judge" and bool(_HASH.fullmatch(value.packet_sha256)), "invalid assessment provenance")
        _fail(len({m.dimension for m in value.measures}) == len(value.measures), "duplicate assessment measure")
        _fail(value.status != "complete" or bool(value.measures) and all(m.value is not None for m in value.measures), "complete assessment needs measured values")
        _fail(value.runtime_evidence_id is None or bool(_ID.fullmatch(value.runtime_evidence_id)), "invalid judge runtime evidence")
    if isinstance(value, HumanReview):
        _fail(value.provenance.kind == "human", "invalid human provenance")
        _fail(all(bool(_HASH.fullmatch(h)) for h in (value.evidence_sha256, value.rubric_sha256)), "invalid review binding")
    if isinstance(value, Score):
        _fail(value.grader.kind == "deterministic", "invalid grader provenance")
        _fail(tuple(m.dimension for m in value.measures) == DIMENSIONS, "score dimensions must be complete and ordered")
        _fail(all(bool(_HASH.fullmatch(h)) for h in (value.run_sha256, value.rubric_sha256)), "invalid score binding")


def _typed(value: object, annotation: object, *, decode: bool = False, depth: int = 0) -> object:
    _fail(depth <= 20, "record depth bound")
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is UnionType:
        for option in args:
            try:
                return _typed(value, option, decode=decode, depth=depth + 1)
            except EvaluationError:
                pass
        raise EvaluationError("invalid union value")
    if origin is Literal:
        _fail(any(type(value) is type(a) and value == a for a in args), "unknown enum value")
        return value
    if origin is tuple:
        _fail(type(value) is (list if decode else tuple) and len(value) <= MAX_ITEMS, "invalid or oversized sequence")
        return tuple(_typed(v, args[0], decode=decode, depth=depth + 1) for v in value)
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        if decode:
            _fail(type(value) is dict and set(value) == set(hints), "unknown record fields")
            result = annotation(**{k: _typed(value[k], t, decode=True, depth=depth + 1) for k, t in hints.items()})
        else:
            _fail(type(value) is annotation, "invalid record type")
            for k, t in hints.items():
                _typed(getattr(value, k), t, depth=depth + 1)
            result = value
        _semantic(result)
        return result
    if annotation is float:
        _fail(type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value), "invalid finite number")
        return value
    _fail(type(value) is annotation, "invalid scalar type")
    if annotation is str:
        _text(value)
    if annotation is int:
        _fail(abs(value) <= 10**15, "integer bound")
    return value


def validate(value: object) -> None:
    _fail(is_dataclass(value) and not isinstance(value, type), "expected typed record")
    _typed(value, type(value))


def digest(value: object) -> str:
    """Canonical content hash; not a signature or proof of observation."""
    raw = asdict(value) if is_dataclass(value) else value
    return sha256(json.dumps(raw, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def encode(value: object) -> str:
    _fail(type(value).__name__ in RECORDS and RECORDS[type(value).__name__] is type(value), "unknown record kind")
    validate(value)
    text = json.dumps({"schema": SCHEMA, "kind": type(value).__name__, "data": asdict(value)}, sort_keys=True, indent=2, allow_nan=False) + "\n"
    _fail(len(text.encode()) <= MAX_BYTES, "record byte bound")
    return text


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for k, v in pairs:
        _fail(k not in result, "duplicate JSON key")
        result[k] = v
    return result


def decode(text: str) -> object:
    _fail(type(text) is str and len(text.encode()) <= MAX_BYTES, "record byte bound")
    try:
        raw = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("nonfinite JSON")))
        _fail(type(raw) is dict and set(raw) == {"schema", "kind", "data"}, "unknown envelope")
        _fail(raw["schema"] == SCHEMA and type(raw["kind"]) is str and raw["kind"] in RECORDS, "unknown schema or kind")
        result = _typed(raw["data"], RECORDS[raw["kind"]], decode=True)
        encode(result)
        return result
    except EvaluationError:
        raise
    except (ValueError, RecursionError, OverflowError) as exc:
        raise EvaluationError("malformed JSON record") from exc


def json_schema() -> dict:
    """Generate the structural wire schema; semantic constraints remain in code."""
    definitions = {}

    def shape(t: object) -> dict:
        origin, args = get_origin(t), get_args(t)
        if origin is UnionType:
            return {"anyOf": [shape(a) for a in args]}
        if origin is Literal:
            return {"enum": list(args)}
        if origin is tuple:
            return {"type": "array", "items": shape(args[0]), "maxItems": MAX_ITEMS}
        if is_dataclass(t):
            name = t.__name__
            if name not in definitions:
                definitions[name] = {}
                hints = get_type_hints(t)
                definitions[name] = {"type": "object", "properties": {k: shape(v) for k, v in hints.items()}, "required": list(hints), "additionalProperties": False}
            return {"$ref": f"#/$defs/{name}"}
        if t is str:
            return {"type": "string", "maxLength": MAX_TEXT}
        if t is int:
            return {"type": "integer", "minimum": -(10**15), "maximum": 10**15}
        return {"type": {float: "number", bool: "boolean", type(None): "null"}[t]}

    variants = [{"type": "object", "properties": {"schema": {"const": SCHEMA}, "kind": {"const": name}, "data": shape(t)}, "required": ["schema", "kind", "data"], "additionalProperties": False} for name, t in RECORDS.items()]
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": SCHEMA, "oneOf": variants, "$defs": definitions}


_GRADER = Provenance("deterministic", "skill-evaluation-checks", "v1", True)


def _input_hash(case: Case) -> str:
    return digest({"prompt": case.prompt, "context": case.context, "assets": [asdict(a) for a in case.assets]})


def _runtime_known(value: RuntimeTuple | None) -> bool:
    return value is not None and all(v is not None for v in asdict(value).values())


def _validity(run: Run) -> tuple[Status, str]:
    if run.evidence is None:
        return run.status, run.reason
    observation = run.evidence.observation
    if run.arm == "do_nothing":
        return "complete", "no_op"
    if observation.status != "complete":
        return observation.status, "observed"
    if run.mode == "fixture":
        return ("complete", "observed") if observation.provenance.kind == "fixture" else ("invalid", "unsafe_evidence")
    if observation.provenance.kind != "runtime" or not observation.runtime_evidence_id or not _runtime_known(observation.observed_runtime) or not _runtime_known(run.intended_runtime):
        return "inconclusive", "runtime_unverified"
    if observation.observed_runtime != run.intended_runtime:
        return "invalid", "tuple_mismatch"
    return "complete", "observed"


def _bindings(case: Case, run: Run, rubric: Rubric) -> None:
    _fail(type(case) is Case and type(run) is Run and type(rubric) is Rubric, "invalid scoring inputs")
    for value in (case, run, rubric):
        validate(value)
    _fail(run.case_sha256 == rubric.case_sha256 == digest(case), "case binding mismatch")
    _fail(run.input_sha256 == _input_hash(case), "input binding mismatch")
    if run.evidence:
        allowed = {a.path for a in case.assets} | set(case.output_paths)
        _fail(all(a.path in allowed for a in run.evidence.assets), "undeclared evidence asset")
        _fail(_validity(run) == (run.status, run.reason), "run validity mismatch")
    if run.arm == "do_nothing":
        _fail(run.evidence is not None and run.evidence.assets == tuple(sorted(case.assets, key=lambda a: a.path)), "invalid do-nothing assets")
        observed = run.evidence.observation
        _fail(observed.provenance.kind == "control" and observed.status == "complete" and not observed.response and observed.events == () and observed.observed_runtime is None and observed.runtime_evidence_id is None and observed.metrics == Metrics(0.0, 0, 0, 0), "invalid do-nothing observation")


def _check(check: Check, case: Case, evidence: Evidence, arm: Arm) -> bool | None:
    observation = evidence.observation
    assets = {a.path: a.text for a in evidence.assets}
    if check.operation == "file_equals":
        return assets.get(check.target) == check.expected
    if check.operation == "response_equals":
        return observation.response == check.expected
    if check.operation == "response_contains":
        return check.expected in observation.response
    if check.operation == "response_max_chars":
        return len(observation.response) <= check.expected
    if check.operation in ("event_present", "event_absent", "skill_activation") and observation.events is None:
        return None
    if check.operation == "event_present":
        return check.target in observation.events
    if check.operation == "event_absent":
        return check.target not in observation.events
    if check.operation == "skill_activation":
        # Controls cannot activate an absent skill; the treatment tests triggering.
        return (f"skill:{check.target}" in observation.events) == (check.expected and arm == "skill")
    if check.operation == "unchanged":
        return assets == {a.path: a.text for a in case.assets}
    if check.operation == "completed":
        return observation.status == "complete"
    metric = getattr(observation.metrics, check.target)
    return None if metric is None else metric <= check.expected


def judge_packet(run: Run, rubric: Rubric) -> JudgePacket:
    """Blind assignment metadata, not semantic clues or the content's own wording."""
    validate(run)
    validate(rubric)
    _fail(run.evidence is not None and run.case_sha256 == rubric.case_sha256, "judge binding mismatch")
    return JudgePacket(digest({"assets": [asdict(a) for a in run.evidence.assets], "response": run.evidence.observation.response}), digest(rubric), run.evidence.assets, run.evidence.observation.response, rubric.judge_instructions)


def score_run(case: Case, run: Run, rubric: Rubric, *, assessment: Assessment | None = None, human_review: HumanReview | None = None, judge: Callable[[JudgePacket], Assessment] | None = None) -> Score:
    """Rescore preserved evidence; an optional judge runs only after checks pass."""
    _bindings(case, run, rubric)
    _fail(not (assessment is not None and judge is not None), "choose one judge source")
    results = ()
    measures = tuple(Measure(d, None) for d in DIMENSIONS)
    status, verdict = run.status, "not_scored"
    if run.evidence is not None and run.status not in ("unavailable", "invalid"):
        results = tuple(CheckResult(c.check_id, c.dimension, _check(c, case, run.evidence, run.arm)) for c in rubric.checks)
        dimension_measures = []
        for dimension in DIMENSIONS:
            values = [v.passed for v in results if v.dimension == dimension]
            measured = bool(values) and all(v is not None for v in values)
            dimension_measures.append(Measure(dimension, sum(values) / len(values) if measured else None))
        measures = tuple(dimension_measures)
        failed = any(v.passed is False for v in results)
        if status == "complete" and any(v.passed is None for v in results):
            status = "partial"
        if failed:
            verdict = "failed"
        elif status == "complete" and all(v.passed is not None for v in results):
            verdict = "passed"
        elif status == "complete":
            status = "partial"
        if judge is not None and verdict == "passed":
            packet = judge_packet(run, rubric)
            try:
                assessment = judge(packet)
                _fail(type(assessment) is Assessment, "invalid judge result")
                validate(assessment)
            except Exception:
                # Provider errors may contain private payloads. Preserve deterministic
                # evidence with an explicitly unavailable, unobserved judge record.
                assessment = Assessment(Provenance("judge", "judge-adapter-error", "v1", True), digest(packet), RuntimeTuple(None, None, None, None, None, None), None, None, "unavailable", ())
        if assessment is not None:
            _fail(type(assessment) is Assessment, "invalid assessment type")
            validate(assessment)
            _fail(verdict == "passed", "judge cannot override deterministic failure or missingness")
            _fail(assessment.packet_sha256 == digest(judge_packet(run, rubric)), "assessment binding mismatch")
            if assessment.status != "complete" or not _runtime_known(assessment.observed_runtime) or assessment.observed_runtime != assessment.intended_runtime or not assessment.runtime_evidence_id:
                status, verdict = "inconclusive", "not_scored"
        elif rubric.judge_required and verdict == "passed":
            status, verdict = "inconclusive", "not_scored"
        if human_review is not None:
            _fail(type(human_review) is HumanReview, "invalid human review type")
            validate(human_review)
            _fail(human_review.evidence_sha256 == run.evidence_sha256 and human_review.rubric_sha256 == digest(rubric), "human review binding mismatch")
            if human_review.decision == "rejected":
                verdict = "failed"
        if case.human_review_required:
            if human_review is None or human_review.decision == "pending":
                status = "inconclusive"
                if verdict != "failed":
                    verdict = "not_scored"
            elif human_review.decision == "rejected":
                verdict = "failed"
    else:
        _fail(assessment is None and human_review is None, "cannot assess absent or invalid evidence")
    result = Score(digest(run), digest(rubric), run.evidence_sha256, _GRADER, status, verdict, results, measures, assessment, human_review)
    encode(result)
    return result


def compare(case: Case, variant: Variant, rubric: Rubric, runs: tuple[Run, ...], scores: tuple[Score, ...]) -> dict:
    """One matched triplet yields descriptive differences, never efficacy proof."""
    validate(variant)
    _fail(len(runs) == len(scores) == 3 and {r.arm for r in runs} == set(ARMS), "comparison needs exactly three arms")
    by_arm = {}
    for run, score in zip(runs, scores):
        _fail(type(score) is Score, "invalid score type")
        validate(score)
        _bindings(case, run, rubric)
        _fail(run.variant_sha256 == digest(variant), "variant binding mismatch")
        _fail(score == score_run(case, run, rubric, assessment=score.assessment, human_review=score.human_review), "score binding mismatch")
        by_arm[run.arm] = (run, score)
    _fail(len({digest(r.intended_runtime) for r in runs}) == 1 and len({r.mode for r in runs}) == 1, "comparison environment mismatch")
    ready = all(r.status == "complete" and s.status == "complete" for r, s in by_arm.values())
    deltas = {}
    for control in ("baseline", "do_nothing"):
        a, b = by_arm["skill"][1], by_arm[control][1]
        pair_measurable = all(by_arm[arm][0].status == "complete" and by_arm[arm][1].status in ("complete", "partial") for arm in ("skill", control))
        deltas[control] = {x.dimension: x.value - y.value if pair_measurable and x.value is not None and y.value is not None else None for x, y in zip(a.measures, b.measures)}
    return {"schema": SCHEMA, "case_sha256": digest(case), "variant_sha256": digest(variant), "rubric_sha256": digest(rubric), "mode": runs[0].mode, "status": "complete" if ready else "inconclusive", "efficacy": "inconclusive", "scope": "one-matched-triplet-no-general-validity", "arms": {arm: {"run_sha256": digest(r), "score_sha256": digest(s), "status": s.status, "verdict": s.verdict} for arm, (r, s) in by_arm.items()}, "skill_minus": deltas, "authority_effect": "none"}

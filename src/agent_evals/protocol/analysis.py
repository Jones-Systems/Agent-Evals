"""Analysis imports are data, never authority to execute a judge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .publication import AdmittedEvidence, ContentRef, Missingness, hash_field, require, validate_content_ref


@dataclass(frozen=True)
class AnalysisSpec:
    rubric: str
    instructions: str
    lower_bound: float
    upper_bound: float


@dataclass(frozen=True)
class NeutralEvidence:
    label: str
    evidence: AdmittedEvidence | None
    missingness: Missingness


@dataclass(frozen=True)
class BlindedAnalysisPacket:
    spec: AnalysisSpec
    items: tuple[NeutralEvidence, ...]


@dataclass(frozen=True)
class NeutralMappingEntry:
    label: str
    trial_id: str
    arm_id: str


@dataclass(frozen=True)
class NeutralLabelMapping:
    result_set_ref: ContentRef
    packet_ref: ContentRef
    entries: tuple[NeutralMappingEntry, ...]


@dataclass(frozen=True)
class AnalysisMeasure:
    label: str
    value: float | None


@dataclass(frozen=True)
class AnalysisResult:
    packet_ref: ContentRef
    mapping_sha256: str
    requested_profile_sha256: str
    observed_profile_sha256: str | None
    status: Literal["pending", "complete", "inconclusive", "unavailable"]
    measures: tuple[AnalysisMeasure, ...]
    revision: int
    predecessor: ContentRef | None


@dataclass(frozen=True)
class ComparisonMeasure:
    trial_id: str
    arm_id: str
    value: float | None


@dataclass(frozen=True)
class UnblindedComparison:
    analysis_result_ref: ContentRef
    mapping_ref: ContentRef
    measures: tuple[ComparisonMeasure, ...]


ANALYSIS_TYPES = {
    AnalysisSpec: "codex.agent-evals-analysis-spec/v1",
    BlindedAnalysisPacket: "codex.agent-evals-blinded-packet/v1",
    NeutralLabelMapping: "codex.agent-evals-neutral-mapping/v1",
    AnalysisResult: "codex.agent-evals-analysis-result/v1",
    UnblindedComparison: "codex.agent-evals-comparison/v1",
}


def validate_analysis_record(value: object) -> None:
    if type(value) is AnalysisSpec:
        require(value.lower_bound <= value.upper_bound, "invalid analysis bounds")
    elif type(value) is BlindedAnalysisPacket:
        validate_analysis_record(value.spec)
        require(bool(value.items), "empty packet")
        require(tuple(item.label for item in value.items) == tuple(f"item-{i:06d}" for i in range(len(value.items))), "noncanonical neutral labels")
        for item in value.items:
            require((item.evidence is None) == (item.missingness == "missing"), "packet missingness mismatch")
    elif type(value) is NeutralLabelMapping:
        validate_content_ref(value.result_set_ref)
        validate_content_ref(value.packet_ref)
        require(value.result_set_ref.record_schema == "codex.agent-evals-result-set/v1", "wrong result set schema")
        require(value.packet_ref.record_schema == ANALYSIS_TYPES[BlindedAnalysisPacket], "wrong packet schema")
        require(tuple(item.label for item in value.entries) == tuple(f"item-{i:06d}" for i in range(len(value.entries))), "noncanonical mapping labels")
        require(len({item.trial_id for item in value.entries}) == len(value.entries), "duplicate mapping trial")
    elif type(value) is AnalysisResult:
        validate_content_ref(value.packet_ref)
        require(value.packet_ref.record_schema == ANALYSIS_TYPES[BlindedAnalysisPacket], "wrong packet schema")
        hash_field(value.mapping_sha256)
        hash_field(value.requested_profile_sha256)
        if value.observed_profile_sha256 is not None:
            hash_field(value.observed_profile_sha256)
        require(value.revision >= 1 and ((value.revision == 1) == (value.predecessor is None)), "analysis predecessor required")
        if value.predecessor is not None:
            validate_content_ref(value.predecessor)
            require(value.predecessor.record_schema == ANALYSIS_TYPES[AnalysisResult], "wrong analysis predecessor")
        require(len({item.label for item in value.measures}) == len(value.measures), "duplicate analysis label")
        require(value.status != "complete" or value.observed_profile_sha256 is not None, "complete analysis lacks observation")
    elif type(value) is UnblindedComparison:
        validate_content_ref(value.analysis_result_ref)
        validate_content_ref(value.mapping_ref)
        require(value.analysis_result_ref.record_schema == ANALYSIS_TYPES[AnalysisResult], "wrong analysis reference")
        require(value.mapping_ref.record_schema == ANALYSIS_TYPES[NeutralLabelMapping], "wrong mapping reference")

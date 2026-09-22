"""Unblinding consumes a persisted terminal result; it never runs analysis."""

from agent_evals.protocol.analysis import AnalysisResult, ComparisonMeasure, UnblindedComparison
from agent_evals.protocol.publication import require
from .blinding import verify_mapping


def _verify_result(store, result, mapping_ref, packet) -> None:
    require(type(result) is AnalysisResult, "expected analysis result")
    require(result.mapping_sha256 == mapping_ref.digest, "analysis mapping mismatch")
    mapping = store.read_record(mapping_ref)
    require(result.packet_ref == mapping.packet_ref, "analysis packet mismatch")
    require(tuple(item.label for item in result.measures) == tuple(item.label for item in packet.items), "analysis label coverage mismatch")
    for measure, item in zip(result.measures, packet.items):
        require(measure.value is None or packet.spec.lower_bound <= measure.value <= packet.spec.upper_bound, "analysis score outside bounds")
        require(item.evidence is not None or measure.value is None, "missing evidence must have null score")
    if result.predecessor is not None:
        previous = store.read_record(result.predecessor)
        require(type(previous) is AnalysisResult and previous.packet_ref == result.packet_ref and previous.mapping_sha256 == result.mapping_sha256, "analysis predecessor mismatch")
        require(previous.revision + 1 == result.revision, "analysis revision mismatch")


def import_analysis_result(store, result: AnalysisResult, mapping_ref, campaign):
    _, packet = verify_mapping(store, mapping_ref, campaign)
    _verify_result(store, result, mapping_ref, packet)
    return store.put_record(result)


def unblind(store, result_ref, mapping_ref, campaign):
    result = store.read_record(result_ref)
    mapping, packet = verify_mapping(store, mapping_ref, campaign)
    _verify_result(store, result, mapping_ref, packet)
    require(result.status in ("complete", "inconclusive", "unavailable"), "analysis is not terminal")
    comparison = UnblindedComparison(result_ref, mapping_ref, tuple(
        ComparisonMeasure(item.trial_id, item.arm_id, measure.value)
        for item, measure in zip(mapping.entries, result.measures)
    ))
    return store.put_record(comparison)

"""Neutralization removes metadata, not semantic clues in admitted content."""

from agent_evals.protocol.analysis import (
    AnalysisSpec, BlindedAnalysisPacket, NeutralEvidence,
    NeutralLabelMapping, NeutralMappingEntry,
)
from agent_evals.protocol.publication import publication_encode, require, validate_publication_record
from agent_evals.publication.result_set import verify_result_set


def _packet_and_entries(store, result_set_ref, campaign, spec: AnalysisSpec):
    validate_publication_record(spec)
    result_set = verify_result_set(store, result_set_ref, campaign)
    trials = {trial.trial_id: trial for trial in campaign.trials}
    admitted = []
    for entry in result_set.entries:
        evidence = store.read_record(entry.evidence_ref) if entry.evidence_ref is not None else None
        key = publication_encode(evidence) if evidence is not None else ""
        admitted.append((key, entry.trial_id, evidence))
    admitted.sort(key=lambda item: (item[0], item[1]))
    packet = BlindedAnalysisPacket(spec, tuple(
        NeutralEvidence(f"item-{index:06d}", evidence, "missing" if evidence is None else "present")
        for index, (_, _, evidence) in enumerate(admitted)
    ))
    entries = tuple(
        NeutralMappingEntry(f"item-{index:06d}", trial_id, trials[trial_id].arm_id)
        for index, (_, trial_id, _) in enumerate(admitted)
    )
    return packet, entries


def blind_result_set(store, result_set_ref, campaign, spec: AnalysisSpec):
    packet, entries = _packet_and_entries(store, result_set_ref, campaign, spec)
    packet_ref = store.put_record(packet)
    mapping = NeutralLabelMapping(result_set_ref, packet_ref, entries)
    return packet_ref, store.put_record(mapping)


def verify_mapping(store, mapping_ref, campaign):
    mapping = store.read_record(mapping_ref)
    require(type(mapping) is NeutralLabelMapping and mapping_ref.access_class == "private", "expected private mapping")
    packet = store.read_record(mapping.packet_ref)
    require(type(packet) is BlindedAnalysisPacket, "wrong mapping packet")
    expected_packet, entries = _packet_and_entries(store, mapping.result_set_ref, campaign, packet.spec)
    expected_mapping = NeutralLabelMapping(mapping.result_set_ref, mapping.packet_ref, entries)
    require(expected_packet == packet and expected_mapping == mapping, "mapping mismatch")
    return mapping, packet

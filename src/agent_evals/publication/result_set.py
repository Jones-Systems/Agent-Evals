"""Sealing verifies imported evidence independently of execution completion."""

from agent_evals.core import digest
from agent_evals.protocol.campaign import ResolvedCampaign, validate_campaign_record
from agent_evals.protocol.publication import (
    AdmittedEvidence, ContentRef, ExecutionResultImport, ResultSetEntry,
    SealedResultSet, require, validate_publication_record,
)


def verify_result_set(store, ref: ContentRef, campaign: ResolvedCampaign) -> SealedResultSet:
    value = store.read_record(ref)
    require(type(value) is SealedResultSet, "expected sealed result set")
    _verify(store, value, campaign)
    return value


def _verify(store, value: SealedResultSet, campaign: ResolvedCampaign) -> None:
    validate_campaign_record(campaign)
    validate_publication_record(value)
    require(value.campaign_sha256 == digest(campaign), "wrong campaign binding")
    trials = {trial.trial_id: trial for trial in campaign.trials}
    require(set(trials) == {entry.trial_id for entry in value.entries}, "wrong trial coverage")
    for entry in value.entries:
        execution = None
        if entry.execution_ref is not None:
            execution = store.read_record(entry.execution_ref)
            require(type(execution) is ExecutionResultImport, "wrong execution reference")
            require(execution.campaign_sha256 == value.campaign_sha256 and execution.trial_id == entry.trial_id, "wrong execution binding")
            require(execution.requested_profile_sha256 == trials[entry.trial_id].requested_profile_sha256, "wrong requested profile binding")
            require(execution.evidence_ref == entry.evidence_ref, "wrong execution evidence binding")
        else:
            require(entry.evidence_ref is None, "evidence without execution binding")
            require(entry.classification == "unavailable", "missing execution classification")
        if entry.evidence_ref is not None:
            require(type(store.read_record(entry.evidence_ref)) is AdmittedEvidence, "wrong evidence reference")
        if entry.classification == "usable":
            require(execution is not None and execution.completion == "complete", "usable execution incomplete")
            require(execution.observed_profile_sha256 == execution.requested_profile_sha256, "usable profile unverified")
    if value.predecessor is not None:
        previous = store.read_record(value.predecessor)
        require(type(previous) is SealedResultSet and previous.campaign_sha256 == value.campaign_sha256, "wrong predecessor binding")
        require(previous.revision + 1 == value.revision, "wrong predecessor revision")


def seal_result_set(store, campaign: ResolvedCampaign, entries: tuple[ResultSetEntry, ...], *, predecessor: ContentRef | None = None) -> ContentRef:
    revision = 1
    if predecessor is not None:
        previous = verify_result_set(store, predecessor, campaign)
        revision = previous.revision + 1
    value = SealedResultSet(digest(campaign), revision, predecessor, tuple(sorted(entries, key=lambda entry: entry.trial_id)))
    _verify(store, value, campaign)
    return store.put_record(value)

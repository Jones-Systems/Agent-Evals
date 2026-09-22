"""Pure deterministic compilation from a campaign specification to trial plans."""

from __future__ import annotations

from dataclasses import asdict
import json

from agent_evals.core import EvaluationError, MAX_BYTES, digest
from agent_evals.protocol.campaign import CampaignSpec, ResolvedCampaign, TrialPlan, campaign_encode, validate_campaign_record
from .counterbalance import arm_order


COMPILER_REVISION = "agent-evals-campaign-compiler-v1"


def _check_budget_bounds(spec: CampaignSpec) -> None:
    policy = {(limit.metric, limit.scope): limit for limit in spec.budget_policy.limits}
    totals: dict[str, int] = {}
    for assignment in spec.profile_matrix:
        for allocation in assignment.allocations:
            trial_limit = policy.get((allocation.metric, "trial"))
            if trial_limit is None or trial_limit.unit != allocation.unit:
                raise EvaluationError("allocation has no matching trial budget")
            if allocation.maximum > trial_limit.maximum:
                raise EvaluationError("trial budget overflow")
            totals[allocation.metric] = totals.get(allocation.metric, 0) + allocation.maximum * spec.repetitions
    for metric, total in totals.items():
        campaign_limit = policy.get((metric, "campaign"))
        if campaign_limit is not None and total > campaign_limit.maximum:
            raise EvaluationError("campaign budget overflow")


def _expected_trials(spec: CampaignSpec, spec_sha256: str) -> tuple[TrialPlan, ...]:
    assignments = {(item.case_id, item.arm_id): item for item in spec.profile_matrix}
    trials: list[TrialPlan] = []
    for case_index, case in enumerate(spec.cases):
        for repetition in range(spec.repetitions):
            for arm_id in arm_order(spec.counterbalance, spec.arms, case_index, repetition):
                assignment = assignments[(case.case_id, arm_id)]
                identity = {
                    "campaign_spec_sha256": spec_sha256,
                    "case_id": case.case_id,
                    "arm_id": arm_id,
                    "repetition": repetition,
                    "stage": "evaluation",
                }
                trials.append(TrialPlan(
                    trial_id=f"trial:{digest(identity)}",
                    case_id=case.case_id,
                    case_sha256=case.case_sha256,
                    input_sha256=case.input_sha256,
                    arm_id=arm_id,
                    repetition=repetition,
                    stage="evaluation",
                    requested_profile=assignment.requested_profile,
                    requested_profile_sha256=assignment.requested_profile.sha256,
                    allocations=assignment.allocations,
                ))
    return tuple(trials)


def _preflight_wire_size(spec: CampaignSpec) -> None:
    """Reject obvious expansion before allocating the complete trial tuple."""
    assignments = {(item.case_id, item.arm_id): item for item in spec.profile_matrix}
    compact_spec_bytes = len(json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")).encode("utf-8"))
    compact_trial_bytes = 0
    for case_index, case in enumerate(spec.cases):
        for arm_id in arm_order(spec.counterbalance, spec.arms, case_index, 0):
            assignment = assignments[(case.case_id, arm_id)]
            trial = TrialPlan(
                f"trial:{'0' * 64}", case.case_id, case.case_sha256,
                case.input_sha256, arm_id, 0, "evaluation",
                assignment.requested_profile, assignment.requested_profile.sha256,
                assignment.allocations,
            )
            compact_trial_bytes += len(json.dumps(asdict(trial), sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if compact_spec_bytes + compact_trial_bytes * spec.repetitions > MAX_BYTES:
        raise EvaluationError("resolved campaign byte bound")


def compile_campaign(spec: CampaignSpec) -> ResolvedCampaign:
    """Resolve order and immutable IDs without creating jobs or spending budgets."""
    validate_campaign_record(spec)
    campaign_encode(spec)
    _check_budget_bounds(spec)
    spec_sha256 = digest(spec)
    _preflight_wire_size(spec)
    result = ResolvedCampaign(
        campaign_spec=spec,
        campaign_spec_sha256=spec_sha256,
        compiler_revision=COMPILER_REVISION,
        counterbalance_revision=spec.counterbalance.algorithm_revision,
        trials=_expected_trials(spec, spec_sha256),
    )
    campaign_encode(result)
    return result

"""Closed campaign specifications and resolved deterministic trial plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
import math
from types import UnionType
from typing import Literal, get_args, get_origin, get_type_hints

from agent_evals.campaign.counterbalance import CounterbalancePolicy, validate_counterbalance
from agent_evals.core import EvaluationError, MAX_BYTES, MAX_TEXT, _ID, _HASH, _pairs, _text, digest
from .budget import BudgetLimit, BudgetPolicy, validate_budget_policy
from .profile import PROFILE_DIMENSIONS, ProfileDimension, RequestedExecutionProfile, validate_requested_profile


CAMPAIGN_SCHEMA = "codex.agent-evals-campaign-spec/v1"
RESOLVED_CAMPAIGN_SCHEMA = "codex.agent-evals-resolved-campaign/v1"
MAX_CAMPAIGN_ITEMS = 256
MAX_TRIALS = 100000


@dataclass(frozen=True)
class CampaignCase:
    case_id: str
    case_sha256: str
    input_sha256: str


@dataclass(frozen=True)
class ProfileAssignment:
    case_id: str
    arm_id: str
    requested_profile: RequestedExecutionProfile
    allocations: tuple[BudgetLimit, ...]


@dataclass(frozen=True)
class OutcomePolicy:
    outcome: Literal["pass_rate", "dimension_score", "resource_use"]
    lower_bound: float
    upper_bound: float
    analysis_unit: Literal["trial", "case"]
    uncertainty_rule: Literal["descriptive_only", "none"]
    failure_rule: Literal["count_as_failure", "report_separately"]
    missingness_rule: Literal["fail_closed", "report_separately"]
    retry_rule: Literal["no_retry", "preserve_trial_identity"]


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    version: int
    suite_sha256: str
    input_sha256: str
    cases: tuple[CampaignCase, ...]
    arms: tuple[str, ...]
    repetitions: int
    profile_matrix: tuple[ProfileAssignment, ...]
    intentionally_varied_dimensions: tuple[ProfileDimension, ...]
    outcome_policy: OutcomePolicy
    budget_policy: BudgetPolicy
    safety_stop_policy_refs: tuple[str, ...]
    counterbalance: CounterbalancePolicy


@dataclass(frozen=True)
class TrialPlan:
    trial_id: str
    case_id: str
    case_sha256: str
    input_sha256: str
    arm_id: str
    repetition: int
    stage: Literal["evaluation"]
    requested_profile: RequestedExecutionProfile
    requested_profile_sha256: str
    allocations: tuple[BudgetLimit, ...]


@dataclass(frozen=True)
class ResolvedCampaign:
    campaign_spec: CampaignSpec
    campaign_spec_sha256: str
    compiler_revision: Literal["agent-evals-campaign-compiler-v1"]
    counterbalance_revision: Literal["agent-evals-counterbalance-v1"]
    trials: tuple[TrialPlan, ...]


TOP_LEVEL = {CampaignSpec: CAMPAIGN_SCHEMA, ResolvedCampaign: RESOLVED_CAMPAIGN_SCHEMA}


def _fail(condition: bool, reason: str) -> None:
    if not condition:
        raise EvaluationError(reason)


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
        _fail(any(type(value) is type(item) and value == item for item in args), "unknown enum value")
        return value
    if origin is tuple:
        _fail(type(value) is (list if decode else tuple) and len(value) <= MAX_TRIALS, "invalid or oversized sequence")
        return tuple(_typed(item, args[0], decode=decode, depth=depth + 1) for item in value)
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        if decode:
            _fail(type(value) is dict and set(value) == set(hints), "unknown record fields")
            return annotation(**{key: _typed(value[key], hint, decode=True, depth=depth + 1) for key, hint in hints.items()})
        _fail(type(value) is annotation, "invalid record type")
        for key, hint in hints.items():
            _typed(getattr(value, key), hint, depth=depth + 1)
        return value
    if annotation is float:
        _fail(type(value) in (int, float) and math.isfinite(value) and abs(value) <= 10**15, "invalid finite number")
        return float(value)
    _fail(type(value) is annotation, "invalid scalar type")
    if annotation is str:
        _text(value)
    if annotation is int:
        _fail(abs(value) <= 10**15, "integer bound")
    return value


def _identifier(value: str) -> None:
    _fail(bool(_ID.fullmatch(value)), "invalid campaign identifier")


def _digest(value: str) -> None:
    _fail(bool(_HASH.fullmatch(value)), "invalid digest")


def _validate_allocations(values: tuple[BudgetLimit, ...]) -> None:
    _fail(len(values) <= MAX_CAMPAIGN_ITEMS, "allocation count bound")
    validate_budget_policy(BudgetPolicy(values, "reject"))
    _fail(all(value.scope == "trial" for value in values), "allocations must have trial scope")


def _validate_spec(spec: CampaignSpec) -> None:
    _identifier(spec.campaign_id)
    _fail(type(spec.version) is int and spec.version > 0, "invalid campaign version")
    _digest(spec.suite_sha256)
    _digest(spec.input_sha256)
    _fail(0 < len(spec.cases) <= MAX_CAMPAIGN_ITEMS, "campaign case count bound")
    _fail(0 < len(spec.arms) <= MAX_CAMPAIGN_ITEMS, "campaign arm count bound")
    _fail(type(spec.repetitions) is int and 0 < spec.repetitions <= MAX_CAMPAIGN_ITEMS, "repetition bound")
    _fail(len(spec.cases) * len(spec.arms) * spec.repetitions <= MAX_TRIALS, "trial count bound")
    case_ids = [case.case_id for case in spec.cases]
    arm_ids = list(spec.arms)
    _fail(len(case_ids) == len(set(case_ids)), "duplicate campaign case")
    _fail(len(arm_ids) == len(set(arm_ids)), "duplicate campaign arm")
    for case in spec.cases:
        _identifier(case.case_id)
        _digest(case.case_sha256)
        _digest(case.input_sha256)
    for arm_id in spec.arms:
        _identifier(arm_id)
    expected = {(case_id, arm_id) for case_id in case_ids for arm_id in arm_ids}
    actual = [(item.case_id, item.arm_id) for item in spec.profile_matrix]
    _fail(len(actual) == len(set(actual)), "duplicate profile assignment")
    _fail(set(actual) == expected, "incomplete requested profile matrix")
    for assignment in spec.profile_matrix:
        validate_requested_profile(assignment.requested_profile)
        _validate_allocations(assignment.allocations)
    varied = tuple(
        dimension for dimension in PROFILE_DIMENSIONS
        if len({getattr(item.requested_profile, dimension) for item in spec.profile_matrix}) > 1
    )
    _fail(spec.intentionally_varied_dimensions == varied, "declared profile variance mismatch")
    _fail(spec.outcome_policy.lower_bound <= spec.outcome_policy.upper_bound, "invalid outcome bounds")
    validate_budget_policy(spec.budget_policy)
    _fail(len(spec.safety_stop_policy_refs) <= MAX_CAMPAIGN_ITEMS, "safety stop count bound")
    _fail(len(set(spec.safety_stop_policy_refs)) == len(spec.safety_stop_policy_refs), "duplicate safety stop policy")
    for policy_ref in spec.safety_stop_policy_refs:
        _identifier(policy_ref)
    validate_counterbalance(spec.counterbalance)


def _validate_resolved(resolved: ResolvedCampaign) -> None:
    _validate_spec(resolved.campaign_spec)
    _fail(resolved.campaign_spec_sha256 == digest(resolved.campaign_spec), "campaign specification digest mismatch")
    expected_count = len(resolved.campaign_spec.cases) * len(resolved.campaign_spec.arms) * resolved.campaign_spec.repetitions
    _fail(len(resolved.trials) == expected_count, "resolved trial coverage mismatch")
    identities: set[tuple[str, str, int, str]] = set()
    trial_ids: set[str] = set()
    for trial in resolved.trials:
        _identifier(trial.trial_id)
        _identifier(trial.case_id)
        _identifier(trial.arm_id)
        _digest(trial.case_sha256)
        _digest(trial.input_sha256)
        validate_requested_profile(trial.requested_profile)
        _fail(trial.requested_profile_sha256 == trial.requested_profile.sha256, "requested profile digest mismatch")
        _validate_allocations(trial.allocations)
        identities.add((trial.case_id, trial.arm_id, trial.repetition, trial.stage))
        trial_ids.add(trial.trial_id)
    _fail(len(identities) == expected_count and len(trial_ids) == expected_count, "duplicate resolved trial")


def validate_campaign_record(value: CampaignSpec | ResolvedCampaign) -> None:
    _typed(value, type(value))
    if type(value) is CampaignSpec:
        _validate_spec(value)
    elif type(value) is ResolvedCampaign:
        _validate_resolved(value)
    else:
        raise EvaluationError("unknown campaign record")


def campaign_encode(value: CampaignSpec | ResolvedCampaign) -> str:
    _fail(type(value) in TOP_LEVEL, "unknown campaign record")
    validate_campaign_record(value)
    text = json.dumps(
        {"schema": TOP_LEVEL[type(value)], "kind": type(value).__name__, "data": asdict(value)},
        sort_keys=True, indent=2, allow_nan=False,
    ) + "\n"
    _fail(len(text.encode("utf-8")) <= MAX_BYTES, "record byte bound")
    return text


def campaign_decode(text: str) -> CampaignSpec | ResolvedCampaign:
    _fail(type(text) is str and len(text.encode("utf-8")) <= MAX_BYTES, "record byte bound")
    try:
        raw = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("nonfinite JSON")))
        _fail(type(raw) is dict and set(raw) == {"schema", "kind", "data"}, "unknown envelope")
        choices = {"CampaignSpec": (CAMPAIGN_SCHEMA, CampaignSpec), "ResolvedCampaign": (RESOLVED_CAMPAIGN_SCHEMA, ResolvedCampaign)}
        _fail(type(raw["kind"]) is str and raw["kind"] in choices, "unknown schema or kind")
        schema, record_type = choices[raw["kind"]]
        _fail(raw["schema"] == schema, "unknown schema or kind")
        result = _typed(raw["data"], record_type, decode=True)
        validate_campaign_record(result)
        if type(result) is ResolvedCampaign:
            from agent_evals.campaign.compiler import compile_campaign
            _fail(result == compile_campaign(result.campaign_spec), "noncanonical resolved campaign")
        campaign_encode(result)
        return result
    except EvaluationError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise EvaluationError("malformed campaign record") from exc


def _schema_for(record_type: type, schema_id: str) -> dict:
    definitions: dict[str, dict] = {}

    def shape(annotation: object) -> dict:
        origin, args = get_origin(annotation), get_args(annotation)
        if origin is UnionType:
            return {"anyOf": [shape(item) for item in args]}
        if origin is Literal:
            return {"enum": list(args)}
        if origin is tuple:
            return {"type": "array", "items": shape(args[0]), "maxItems": MAX_TRIALS}
        if is_dataclass(annotation):
            name = annotation.__name__
            if name not in definitions:
                definitions[name] = {}
                hints = get_type_hints(annotation)
                definitions[name] = {
                    "type": "object", "properties": {key: shape(hint) for key, hint in hints.items()},
                    "required": list(hints), "additionalProperties": False,
                }
            return {"$ref": f"#/$defs/{name}"}
        if annotation is str:
            return {"type": "string", "maxLength": MAX_TEXT}
        if annotation is int:
            return {"type": "integer", "minimum": -(10**15), "maximum": 10**15}
        return {"type": {float: "number", bool: "boolean", type(None): "null"}[annotation]}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": schema_id,
        "type": "object",
        "properties": {"schema": {"const": schema_id}, "kind": {"const": record_type.__name__}, "data": shape(record_type)},
        "required": ["schema", "kind", "data"], "additionalProperties": False, "$defs": definitions,
    }


def campaign_json_schema() -> dict:
    return _schema_for(CampaignSpec, CAMPAIGN_SCHEMA)


def resolved_campaign_json_schema() -> dict:
    return _schema_for(ResolvedCampaign, RESOLVED_CAMPAIGN_SCHEMA)

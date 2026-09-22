"""Deterministic, revisioned arm ordering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_evals.core import EvaluationError


COUNTERBALANCE_REVISION = "agent-evals-counterbalance-v1"


@dataclass(frozen=True)
class CounterbalancePolicy:
    method: Literal["fixed", "balanced_rotation"]
    seed: int
    algorithm_revision: Literal["agent-evals-counterbalance-v1"] = COUNTERBALANCE_REVISION


def validate_counterbalance(policy: CounterbalancePolicy) -> None:
    if type(policy) is not CounterbalancePolicy:
        raise EvaluationError("invalid counterbalance policy")
    if policy.method not in ("fixed", "balanced_rotation") or policy.algorithm_revision != COUNTERBALANCE_REVISION:
        raise EvaluationError("unknown counterbalance value")
    if type(policy.seed) is not int or not 0 <= policy.seed <= 2**31 - 1:
        raise EvaluationError("counterbalance seed bound")


def arm_order(policy: CounterbalancePolicy, arms: tuple[str, ...], case_index: int, repetition: int) -> tuple[str, ...]:
    validate_counterbalance(policy)
    if policy.method == "fixed":
        return arms
    offset = (policy.seed + case_index + repetition) % len(arms)
    return arms[offset:] + arms[:offset]

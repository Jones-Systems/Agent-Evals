"""Closed compile-time budget bounds; this module does not track live spend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_evals.core import EvaluationError


BudgetMetric = Literal["input_tokens", "output_tokens", "tool_calls", "wall_time"]
BudgetScope = Literal["trial", "campaign"]
BudgetUnit = Literal["tokens", "calls", "milliseconds"]
ExhaustionDisposition = Literal["reject", "mark_incomplete"]


@dataclass(frozen=True)
class BudgetLimit:
    metric: BudgetMetric
    scope: BudgetScope
    unit: BudgetUnit
    maximum: int


@dataclass(frozen=True)
class BudgetPolicy:
    limits: tuple[BudgetLimit, ...]
    exhaustion_disposition: ExhaustionDisposition


def validate_budget_policy(policy: BudgetPolicy) -> None:
    if type(policy) is not BudgetPolicy or not policy.limits:
        raise EvaluationError("invalid budget policy")
    if policy.exhaustion_disposition not in ("reject", "mark_incomplete"):
        raise EvaluationError("unknown budget exhaustion disposition")
    seen: set[tuple[str, str]] = set()
    expected_units = {
        "input_tokens": "tokens",
        "output_tokens": "tokens",
        "tool_calls": "calls",
        "wall_time": "milliseconds",
    }
    for limit in policy.limits:
        if type(limit) is not BudgetLimit or type(limit.maximum) is not int or not 0 <= limit.maximum <= 10**15:
            raise EvaluationError("invalid budget limit")
        if limit.metric not in expected_units or limit.scope not in ("trial", "campaign"):
            raise EvaluationError("unknown budget limit value")
        if limit.unit != expected_units[limit.metric]:
            raise EvaluationError("budget unit mismatch")
        key = (limit.metric, limit.scope)
        if key in seen:
            raise EvaluationError("duplicate budget limit")
        seen.add(key)

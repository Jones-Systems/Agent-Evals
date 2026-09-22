"""Requested execution identity, deliberately separate from observations."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

from agent_evals.core import EvaluationError, _ID, digest


ProfileDimension = Literal[
    "provider", "surface", "model", "model_revision", "reasoning",
    "runtime", "runtime_revision", "agent_topology", "tool_policy_ref",
    "network_policy_ref",
]
PROFILE_DIMENSIONS = tuple(ProfileDimension.__args__)


@dataclass(frozen=True)
class RequestedExecutionProfile:
    provider: str
    surface: str
    model: str
    model_revision: str
    reasoning: str
    runtime: str
    runtime_revision: str
    agent_topology: str
    tool_policy_ref: str
    network_policy_ref: str

    @property
    def sha256(self) -> str:
        """Return canonical requested-profile identity, not observed identity."""
        validate_requested_profile(self)
        return digest(self)


def validate_requested_profile(profile: RequestedExecutionProfile) -> None:
    if type(profile) is not RequestedExecutionProfile:
        raise EvaluationError("invalid requested profile")
    if tuple(field.name for field in fields(profile)) != PROFILE_DIMENSIONS:
        raise EvaluationError("invalid requested profile fields")
    if not all(_ID.fullmatch(getattr(profile, field)) for field in PROFILE_DIMENSIONS):
        raise EvaluationError("invalid requested profile identifier")

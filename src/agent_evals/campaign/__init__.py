"""Deterministic campaign compilation."""

from .counterbalance import COUNTERBALANCE_REVISION, CounterbalancePolicy

COMPILER_REVISION = "agent-evals-campaign-compiler-v1"


def compile_campaign(spec):
    from .compiler import compile_campaign as implementation
    return implementation(spec)

__all__ = [
    "COMPILER_REVISION", "COUNTERBALANCE_REVISION", "CounterbalancePolicy",
    "compile_campaign",
]

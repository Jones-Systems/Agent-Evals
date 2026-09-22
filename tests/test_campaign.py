"""Contract tests for deterministic, workspace-independent campaign compilation."""

from __future__ import annotations

from dataclasses import asdict, replace
from importlib import resources
import json
from pathlib import Path
import unittest

import agent_evals as e


H = "a" * 64
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def profile(model: str) -> e.RequestedExecutionProfile:
    return e.RequestedExecutionProfile(
        "example-provider", "api", model, "r1", "medium", "agent-runtime",
        "r1", "single", "tools:v1", "network:none",
    )


def make_spec(*, method: str = "fixed", repetitions: int = 2) -> e.CampaignSpec:
    cases = (
        e.CampaignCase("case-a", "a" * 64, "b" * 64),
        e.CampaignCase("case-b", "c" * 64, "d" * 64),
    )
    arms = ("baseline", "skill", "control")
    assignments = tuple(
        e.ProfileAssignment(
            case.case_id,
            arm,
            profile("model-b" if arm == "skill" else "model-a"),
            (
                e.BudgetLimit("input_tokens", "trial", "tokens", 10),
                e.BudgetLimit("tool_calls", "trial", "calls", 2),
            ),
        )
        for case in cases for arm in arms
    )
    return e.CampaignSpec(
        "campaign-fixture", 1, H, "e" * 64, cases, arms, repetitions,
        assignments, ("model",),
        e.OutcomePolicy(
            "pass_rate", 0.0, 1.0, "trial", "descriptive_only",
            "count_as_failure", "report_separately", "no_retry",
        ),
        e.BudgetPolicy((
            e.BudgetLimit("input_tokens", "trial", "tokens", 20),
            e.BudgetLimit("input_tokens", "campaign", "tokens", 1000),
            e.BudgetLimit("tool_calls", "trial", "calls", 4),
            e.BudgetLimit("tool_calls", "campaign", "calls", 1000),
        ), "reject"),
        ("safety:default-v1",),
        e.CounterbalancePolicy(method, 1),
    )


class CampaignCompilerTests(unittest.TestCase):
    def test_canonical_replay_digests_and_requested_only_profile(self) -> None:
        spec = make_spec()
        self.assertEqual(e.campaign_decode((FIXTURES / "campaign-spec.json").read_text(encoding="utf-8")), spec)
        resolved = e.compile_campaign(spec)
        self.assertEqual(e.campaign_decode(e.campaign_encode(spec)), spec)
        self.assertEqual(e.campaign_decode(e.campaign_encode(resolved)), resolved)
        self.assertEqual(e.compile_campaign(spec), resolved)
        self.assertEqual(resolved.campaign_spec_sha256, e.digest(spec))
        self.assertEqual(spec.profile_matrix[0].requested_profile.sha256, e.digest(spec.profile_matrix[0].requested_profile))
        self.assertNotIn("observed", asdict(spec.profile_matrix[0].requested_profile))

    def test_trial_ids_are_stable_unique_and_cover_the_matrix(self) -> None:
        spec = make_spec(repetitions=3)
        first = e.compile_campaign(spec)
        second = e.compile_campaign(spec)
        self.assertEqual([trial.trial_id for trial in first.trials], [trial.trial_id for trial in second.trials])
        self.assertEqual(len({trial.trial_id for trial in first.trials}), 18)
        self.assertEqual(
            {(trial.case_id, trial.arm_id, trial.repetition) for trial in first.trials},
            {(case.case_id, arm, repetition) for case in spec.cases for arm in spec.arms for repetition in range(3)},
        )
        self.assertEqual(len({trial.input_sha256 for trial in first.trials}), 2)
        self.assertTrue(all("job" not in trial.trial_id and "attempt" not in trial.trial_id for trial in first.trials))

    def test_fixed_and_balanced_orders_include_incomplete_rotations(self) -> None:
        fixed = e.compile_campaign(make_spec(method="fixed", repetitions=2))
        self.assertEqual([trial.arm_id for trial in fixed.trials[:6]], ["baseline", "skill", "control"] * 2)
        balanced = e.compile_campaign(make_spec(method="balanced_rotation", repetitions=4))
        case_a = [trial.arm_id for trial in balanced.trials if trial.case_id == "case-a"]
        self.assertEqual(case_a, [
            "skill", "control", "baseline",
            "control", "baseline", "skill",
            "baseline", "skill", "control",
            "skill", "control", "baseline",
        ])
        self.assertEqual(len(case_a), 12)

    def test_closed_decoding_rejects_unknown_duplicate_nonfinite_and_oversize(self) -> None:
        encoded = e.campaign_encode(make_spec())
        raw = json.loads(encoded)
        failures = (
            json.dumps({**raw, "extra": 1}),
            encoded.replace('"version": 1', '"version": 1, "version": 2', 1),
            encoded.replace('"method": "fixed"', '"method": "random"'),
            encoded.replace('"lower_bound": 0.0', '"lower_bound": NaN'),
            encoded.replace('"provider": "example-provider"', '"observed_provider": "example-provider", "provider": "example-provider"', 1),
            encoded + (" " * e.MAX_BYTES),
        )
        for value in failures:
            with self.subTest(size=len(value)), self.assertRaises(e.EvaluationError):
                e.campaign_decode(value)

    def test_invalid_bounds_duplicates_and_undeclared_variance_fail(self) -> None:
        spec = make_spec()
        duplicate_case = replace(spec, cases=(spec.cases[0], spec.cases[0]))
        duplicate_arm = replace(spec, arms=("baseline", "baseline"))
        duplicate_assignment = replace(spec, profile_matrix=spec.profile_matrix + (spec.profile_matrix[0],))
        duplicate_limit = replace(
            spec,
            budget_policy=e.BudgetPolicy(spec.budget_policy.limits + (spec.budget_policy.limits[0],), "reject"),
        )
        undeclared = replace(spec, intentionally_varied_dimensions=())
        bad_outcome = replace(spec, outcome_policy=replace(spec.outcome_policy, lower_bound=2.0, upper_bound=1.0))
        bad_seed = replace(spec, counterbalance=e.CounterbalancePolicy("fixed", -1))
        for value in (duplicate_case, duplicate_arm, duplicate_assignment, duplicate_limit, undeclared, bad_outcome, bad_seed, replace(spec, repetitions=0)):
            with self.subTest(value=type(value).__name__), self.assertRaises(e.EvaluationError):
                e.compile_campaign(value)

    def test_budget_overflow_and_unbounded_allocations_fail_at_compile_time(self) -> None:
        spec = make_spec()
        trial_overflow = replace(
            spec,
            profile_matrix=tuple(
                replace(item, allocations=(e.BudgetLimit("input_tokens", "trial", "tokens", 21),))
                for item in spec.profile_matrix
            ),
            intentionally_varied_dimensions=("model",),
        )
        campaign_overflow = replace(
            spec,
            budget_policy=replace(
                spec.budget_policy,
                limits=tuple(
                    replace(limit, maximum=1) if limit.metric == "input_tokens" and limit.scope == "campaign" else limit
                    for limit in spec.budget_policy.limits
                ),
            ),
        )
        no_bound = replace(
            spec,
            profile_matrix=tuple(
                replace(item, allocations=(e.BudgetLimit("output_tokens", "trial", "tokens", 1),))
                for item in spec.profile_matrix
            ),
        )
        for value in (trial_overflow, campaign_overflow, no_bound):
            with self.assertRaises(e.EvaluationError):
                e.compile_campaign(value)

    def test_packaged_schemas_match_generated_contracts(self) -> None:
        package = resources.files("agent_evals").joinpath("resources")
        self.assertEqual(
            json.loads(package.joinpath("campaign-spec-v1.schema.json").read_text(encoding="utf-8")),
            e.campaign_json_schema(),
        )
        self.assertEqual(
            json.loads(package.joinpath("resolved-campaign-v1.schema.json").read_text(encoding="utf-8")),
            e.resolved_campaign_json_schema(),
        )

    def test_resolved_decoder_rejects_tampered_order_or_trial_identity(self) -> None:
        resolved = e.compile_campaign(make_spec())
        tampered = replace(
            resolved,
            trials=(replace(resolved.trials[0], case_sha256="f" * 64),) + resolved.trials[1:],
        )
        with self.assertRaises(e.EvaluationError):
            e.validate_campaign_record(tampered)
        with self.assertRaises(e.EvaluationError):
            e.campaign_encode(tampered)
        raw = json.loads(e.campaign_encode(resolved))
        raw["data"]["trials"][0]["trial_id"] = "trial:tampered"
        with self.assertRaises(e.EvaluationError):
            e.campaign_decode(json.dumps(raw))

    def test_compile_rejects_wire_amplification_before_returning_a_result(self) -> None:
        spec = make_spec()
        arms = tuple(f"arm-{index}" for index in range(64))
        assignments = tuple(
            e.ProfileAssignment(
                spec.cases[0].case_id, arm, profile("model-a"),
                (e.BudgetLimit("input_tokens", "trial", "tokens", 1),),
            )
            for arm in arms
        )
        large = replace(
            spec, cases=(spec.cases[0],), arms=arms, repetitions=64,
            profile_matrix=assignments, intentionally_varied_dimensions=(),
            budget_policy=replace(
                spec.budget_policy,
                limits=tuple(
                    replace(limit, maximum=10000)
                    if limit.metric == "input_tokens" and limit.scope == "campaign"
                    else limit
                    for limit in spec.budget_policy.limits
                ),
            ),
        )
        self.assertLess(len(e.campaign_encode(large).encode("utf-8")), e.MAX_BYTES)
        with self.assertRaisesRegex(e.EvaluationError, "resolved campaign byte bound"):
            e.compile_campaign(large)

    def test_decoder_sanitizes_invalid_unicode(self) -> None:
        with self.assertRaisesRegex(e.EvaluationError, "invalid Unicode text"):
            e.campaign_decode("\ud800")


if __name__ == "__main__":
    unittest.main()

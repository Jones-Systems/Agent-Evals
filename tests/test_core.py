"""Network-free parity tests for the portable deterministic core."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from importlib import resources
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent_evals as e
from agent_evals.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FIXTURE_SOURCE = e.Provenance("fixture", "backlog-behavior-pilot", "v1", True)
RUNTIME = e.RuntimeTuple(None, None, None, None, None, None)
VARIANT = e.Variant(
    "fixture-backlog",
    1,
    FIXTURE_SOURCE,
    "backlog",
    "---\nname: backlog\ndescription: Synthetic test skill.\n---\n# Fixture skill\n",
)


def load_catalog(name: str, record_type: type) -> tuple[object, ...]:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    records = tuple(e.decode(json.dumps(item)) for item in raw)
    if not all(type(record) is record_type for record in records):
        raise AssertionError("fixture catalog contains an unexpected record kind")
    return records


def input_digest(case: e.Case) -> str:
    return e.digest({
        "prompt": case.prompt,
        "context": case.context,
        "assets": [asdict(asset) for asset in case.assets],
    })


def make_run(case: e.Case, arm: str) -> e.Run:
    if arm == "do_nothing":
        observation = e.Observation(
            e.Provenance("control", "do-nothing", "v1", True),
            None,
            None,
            "complete",
            "",
            (),
            e.Metrics(0.0, 0, 0, 0),
        )
    else:
        observation = e.Observation(
            FIXTURE_SOURCE,
            RUNTIME,
            "fixture-observation",
            "complete",
            "ready",
            (),
            e.Metrics(None, None, None, 0),
        )
    evidence = e.Evidence(observation, tuple(sorted(case.assets, key=lambda asset: asset.path)))
    return e.Run(
        f"{case.case_id}-{arm}",
        e.digest(case),
        e.digest(VARIANT),
        input_digest(case),
        arm,
        "fixture",
        RUNTIME,
        "complete",
        "no_op" if arm == "do_nothing" else "observed",
        evidence,
        e.digest(evidence),
    )


class PortableCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_catalog("cases.json", e.Case)
        cls.rubrics = load_catalog("rubrics.json", e.Rubric)
        cls.case = cls.cases[-1]
        cls.rubric = cls.rubrics[-1]

    def test_fixture_catalogs_preserve_trigger_classes_and_bindings(self) -> None:
        self.assertEqual(
            {case.trigger for case in self.cases},
            {"explicit", "implicit", "contextual", "near_miss", "negative_control"},
        )
        self.assertEqual(len(self.cases), len(self.rubrics), 5)
        for case, rubric in zip(self.cases, self.rubrics):
            self.assertEqual(rubric.case_sha256, e.digest(case))

    def test_wire_roundtrip_hashes_and_packaged_schema_match(self) -> None:
        for record in (*self.cases, *self.rubrics, VARIANT):
            with self.subTest(kind=type(record).__name__):
                encoded = e.encode(record)
                self.assertEqual(record, e.decode(encoded))
                self.assertEqual(e.digest(record), e.digest(e.decode(encoded)))
        schema = resources.files("agent_evals").joinpath(
            "resources/skill-evaluation-v1.schema.json"
        ).read_text(encoding="utf-8")
        self.assertEqual(json.loads(schema), e.json_schema())

    def test_unknown_fields_duplicate_keys_bounds_and_secrets_fail_closed(self) -> None:
        raw = json.loads(e.encode(self.case))
        bad = [
            json.dumps({**raw, "schema": "v99"}),
            json.dumps({**raw, "extra": 1}),
            json.dumps({**raw, "data": {**raw["data"], "private_transcript": "no"}}),
            e.encode(self.case).replace('"version": 1', '"version": 1, "version": 2'),
        ]
        for text in bad:
            with self.subTest(size=len(text)), self.assertRaises(e.EvaluationError):
                e.decode(text)
        for value in (
            replace(self.case, version=True),
            replace(self.case, prompt="x" * (e.MAX_TEXT + 1)),
            replace(self.case, context="bad\x1btext"),
            replace(self.case, prompt="ghp_" + "synthetic" * 4),
            e.Metrics(float("nan"), None, None, None),
        ):
            with self.subTest(kind=type(value).__name__), self.assertRaises(e.EvaluationError):
                e.validate(value)

    def test_unsafe_aliasing_and_overlapping_asset_paths_are_rejected(self) -> None:
        for path in ("../escape", "/absolute", ".git/config", "a//b", "a/./b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(e.EvaluationError):
                e.validate(replace(self.case, assets=(e.Asset(path, "safe"),)))
        for paths in (("A.txt", "a.txt"), ("a", "a/b")):
            with self.assertRaises(e.EvaluationError):
                e.validate(replace(
                    self.case,
                    assets=tuple(e.Asset(path, "safe") for path in paths),
                ))

    def test_deterministic_grading_blinding_and_comparison(self) -> None:
        runs = tuple(make_run(self.case, arm) for arm in e.ARMS)
        scores = tuple(e.score_run(self.case, run, self.rubric) for run in runs)
        self.assertEqual([score.verdict for score in scores], ["passed", "passed", "failed"])
        baseline_packet = e.judge_packet(runs[0], self.rubric)
        skill_packet = e.judge_packet(runs[1], self.rubric)
        self.assertEqual(baseline_packet, skill_packet)
        self.assertEqual(
            set(asdict(baseline_packet)),
            {"evidence_sha256", "rubric_sha256", "assets", "response", "instructions"},
        )
        result = e.compare(self.case, VARIANT, self.rubric, runs, scores)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["efficacy"], "inconclusive")
        self.assertEqual(result["authority_effect"], "none")

    def test_human_and_judge_results_bind_exact_preserved_evidence(self) -> None:
        run = make_run(self.case, "baseline")
        rubric = replace(
            self.rubric,
            judge_required=True,
            judge_instructions="Assess only admitted public evidence.",
        )
        self.assertEqual(e.score_run(self.case, run, rubric).status, "inconclusive")
        judge_runtime = e.RuntimeTuple("example", "judge", "r1", "high", "test", "r1")
        assessment = e.Assessment(
            e.Provenance("judge", "judge-test", "v1", True),
            e.digest(e.judge_packet(run, rubric)),
            judge_runtime,
            judge_runtime,
            "judge-observation",
            "complete",
            (e.Measure("quality", 0.5),),
        )
        self.assertEqual(
            e.score_run(self.case, run, rubric, assessment=assessment).status,
            "complete",
        )
        review = e.HumanReview(
            e.Provenance("human", "review-test", "v1", True),
            run.evidence_sha256,
            e.digest(rubric),
            "rejected",
        )
        self.assertEqual(
            e.score_run(
                self.case, run, rubric, assessment=assessment, human_review=review
            ).verdict,
            "failed",
        )
        with self.assertRaises(e.EvaluationError):
            e.score_run(
                self.case,
                run,
                rubric,
                assessment=replace(assessment, packet_sha256="0" * 64),
            )
        with self.assertRaises(e.EvaluationError):
            e.score_run(
                self.case,
                run,
                rubric,
                assessment=assessment,
                human_review=replace(review, rubric_sha256="0" * 64),
            )

    def test_evidence_input_rubric_and_control_tampering_are_rejected(self) -> None:
        runs = tuple(make_run(self.case, arm) for arm in e.ARMS)
        changed = replace(
            runs[0].evidence,
            observation=replace(runs[0].evidence.observation, response="changed"),
        )
        for run in (
            replace(runs[0], evidence=changed),
            replace(runs[0], case_sha256="0" * 64),
            replace(runs[0], input_sha256="0" * 64),
        ):
            with self.subTest(run=run.run_id), self.assertRaises(e.EvaluationError):
                e.score_run(self.case, run, self.rubric)
        with self.assertRaises(e.EvaluationError):
            e.score_run(
                self.case,
                runs[0],
                replace(self.rubric, case_sha256="0" * 64),
            )

        forged_control_evidence = replace(
            runs[2].evidence,
            observation=replace(
                runs[2].evidence.observation,
                provenance=FIXTURE_SOURCE,
                response="ready",
            ),
        )
        forged_control = replace(
            runs[2],
            evidence=forged_control_evidence,
            evidence_sha256=e.digest(forged_control_evidence),
        )
        with self.assertRaises(e.EvaluationError):
            e.score_run(self.case, forged_control, self.rubric)

        extra_asset_evidence = replace(
            runs[0].evidence,
            assets=runs[0].evidence.assets + (e.Asset("unlisted.txt", "synthetic"),),
        )
        with self.assertRaises(e.EvaluationError):
            e.score_run(
                self.case,
                replace(
                    runs[0],
                    evidence=extra_asset_evidence,
                    evidence_sha256=e.digest(extra_asset_evidence),
                ),
                self.rubric,
            )

    def test_missing_events_metrics_and_partial_evidence_remain_missing(self) -> None:
        run = make_run(self.case, "skill")
        missing_events_evidence = replace(
            run.evidence,
            observation=replace(run.evidence.observation, events=None),
        )
        missing_events = replace(
            run,
            evidence=missing_events_evidence,
            evidence_sha256=e.digest(missing_events_evidence),
        )
        score = e.score_run(self.case, missing_events, self.rubric)
        self.assertEqual((score.status, score.verdict), ("partial", "not_scored"))
        self.assertIsNone(score.measures[1].value)

        metric_rubric = replace(
            self.rubric,
            checks=self.rubric.checks
            + (e.Check("latency", "efficiency", "metric_at_most", "wall_ms", 100),),
        )
        metric_score = e.score_run(self.case, run, metric_rubric)
        self.assertEqual(metric_score.status, "partial")
        self.assertIsNone(metric_score.measures[-1].value)

        partial_evidence = replace(
            run.evidence,
            observation=replace(run.evidence.observation, status="partial"),
        )
        partial_run = replace(
            run,
            status="partial",
            evidence=partial_evidence,
            evidence_sha256=e.digest(partial_evidence),
        )
        self.assertEqual(
            e.score_run(self.case, partial_run, self.rubric).status,
            "partial",
        )

    def test_judge_failure_is_sanitized_without_losing_checks(self) -> None:
        run = make_run(self.case, "baseline")
        judge = Mock(side_effect=RuntimeError("private provider response"))
        score = e.score_run(self.case, run, self.rubric, judge=judge)
        self.assertEqual((score.status, score.verdict), ("inconclusive", "not_scored"))
        self.assertEqual(score.assessment.status, "unavailable")
        self.assertEqual(score.measures[0].value, 1.0)
        self.assertNotIn("private provider", e.encode(score))
        self.assertEqual(
            score,
            e.score_run(self.case, run, self.rubric, assessment=score.assessment),
        )

    def test_comparison_rejects_arm_variant_score_and_type_tampering(self) -> None:
        runs = tuple(make_run(self.case, arm) for arm in e.ARMS)
        scores = tuple(e.score_run(self.case, run, self.rubric) for run in runs)
        forged_type = replace(
            scores[0],
            measures=(e.Measure("quality", True), *scores[0].measures[1:]),
        )
        candidates = (
            ((runs[0], runs[0], runs[2]), scores),
            (runs, tuple(reversed(scores))),
            (runs, (replace(scores[0], verdict="failed"), *scores[1:])),
            (runs, (forged_type, *scores[1:])),
        )
        for bad_runs, bad_scores in candidates:
            with self.assertRaises(e.EvaluationError):
                e.compare(
                    self.case,
                    VARIANT,
                    self.rubric,
                    bad_runs,
                    bad_scores,
                )
        with self.assertRaises(e.EvaluationError):
            e.compare(
                self.case,
                replace(VARIANT, version=2),
                self.rubric,
                runs,
                scores,
            )

    def test_persisted_result_record_round_trips(self) -> None:
        run = make_run(self.case, "baseline")
        rubric = replace(
            self.rubric,
            judge_required=True,
            judge_instructions="Assess admitted public evidence.",
        )
        judge_runtime = e.RuntimeTuple("example", "judge", "r1", "high", "test", "r1")
        assessment = e.Assessment(
            e.Provenance("judge", "judge-test", "v1", True),
            e.digest(e.judge_packet(run, rubric)),
            judge_runtime,
            judge_runtime,
            "judge-observation",
            "complete",
            (e.Measure("quality", 0.5),),
        )
        review = e.HumanReview(
            e.Provenance("human", "review-test", "v1", True),
            run.evidence_sha256,
            e.digest(rubric),
            "approved",
        )
        score = e.score_run(
            self.case,
            run,
            rubric,
            assessment=assessment,
            human_review=review,
        )
        for record in (run, score, assessment, review):
            with self.subTest(kind=type(record).__name__):
                self.assertEqual(e.decode(e.encode(record)), record)

    def test_portable_surface_has_no_execution_or_workspace_api(self) -> None:
        self.assertFalse(hasattr(e, "AgentInput"))
        self.assertFalse(hasattr(e, "run_case"))
        self.assertFalse(hasattr(e, "Adapter"))

    def test_cli_schema_parse_grade_and_sanitized_failure(self) -> None:
        run = make_run(self.case, "baseline")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "case": root / "case.json",
                "run": root / "run.json",
                "rubric": root / "rubric.json",
            }
            for name, record in (
                ("case", self.case), ("run", run), ("rubric", self.rubric)
            ):
                paths[name].write_text(e.encode(record), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["schema"]), 0)
            self.assertEqual(json.loads(output.getvalue()), e.json_schema())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["parse", str(paths["case"])]), 0)
            self.assertEqual(e.decode(output.getvalue()), self.case)

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "grade",
                    "--case", str(paths["case"]),
                    "--run", str(paths["run"]),
                    "--rubric", str(paths["rubric"]),
                ]), 0)
            self.assertEqual(e.decode(output.getvalue()).verdict, "passed")

            paths["case"].write_text("private malformed payload", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(main(["parse", str(paths["case"])]), 2)
            self.assertNotIn("private malformed payload", errors.getvalue())
            self.assertEqual(json.loads(errors.getvalue())["authority_effect"], "none")

    def test_cli_reader_refuses_symlinks_and_nonregular_files(self) -> None:
        from agent_evals.cli import _read_record

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "case.json"
            regular.write_text(e.encode(self.case), encoding="utf-8")
            alias = root / "alias.json"
            alias.symlink_to(regular)
            for path in (alias, root):
                with self.subTest(path=path.name), self.assertRaises(e.EvaluationError):
                    _read_record(str(path))

    def test_cli_reader_bounds_growth_on_one_descriptor_and_always_closes(self) -> None:
        from agent_evals.cli import _read_record

        descriptor = 17
        metadata = os.stat_result(
            (stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, 1, 0, 0, 0)
        )
        with (
            patch("agent_evals.cli.os.open", return_value=descriptor) as opened,
            patch("agent_evals.cli.os.fstat", return_value=metadata),
            patch(
                "agent_evals.cli.os.read",
                return_value=b"x" * (e.MAX_BYTES + 1),
            ) as read,
            patch("agent_evals.cli.os.close") as closed,
        ):
            with self.assertRaises(e.EvaluationError):
                _read_record("selected.json")
        opened.assert_called_once()
        read.assert_called_once_with(descriptor, e.MAX_BYTES + 1)
        closed.assert_called_once_with(descriptor)


if __name__ == "__main__":
    unittest.main()

"""Network-free parity tests for the portable deterministic core."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from importlib import resources
import io
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()

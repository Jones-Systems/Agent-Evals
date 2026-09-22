"""End-to-end contracts for the closed-JSON, workspace-independent CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import ast
import io
import json
from pathlib import Path
import tempfile
import unittest

import agent_evals as e
from agent_evals.cli import CAPABILITIES_SCHEMA, CLI_SCHEMA, CONFORMANCE_SCHEMA, main
from test_campaign import make_spec
from test_core import VARIANT, load_catalog, make_run


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def invoke(arguments: list[str]) -> tuple[int, dict, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    lines = stdout.getvalue().splitlines()
    if len(lines) != 1:
        raise AssertionError(f"expected one stdout document, got {len(lines)}")
    return code, json.loads(lines[0]), stderr.getvalue()


def write(path: Path, value: str | object) -> None:
    text = value if type(value) is str else json.dumps(value, sort_keys=True)
    path.write_text(text, encoding="utf-8")


class OfflineCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_schema_capabilities_conformance_and_unavailable_exit(self) -> None:
        code, listing, stderr = invoke(["schema", "list"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn(e.CAMPAIGN_SCHEMA, listing["schemas"])
        code, schema, _ = invoke(["schema", "show", "--name", e.CAMPAIGN_SCHEMA])
        self.assertEqual(code, 0)
        self.assertEqual(schema, e.campaign_json_schema())

        code, capabilities, _ = invoke(["capabilities"])
        self.assertEqual((code, capabilities["schema"]), (0, CAPABILITIES_SCHEMA))
        self.assertTrue(capabilities["offline"]["available"])
        for name in ("workspace", "execution", "serving", "connectors", "network_effects"):
            self.assertFalse(capabilities[name]["available"])

        code, result, _ = invoke(["conformance"])
        self.assertEqual((code, result["schema"], result["status"]), (0, CONFORMANCE_SCHEMA, "complete"))
        code, result, stderr = invoke(["execute"])
        self.assertEqual((code, result["status"]), (3, "unavailable"))
        self.assertNotIn(str(self.root), stderr)

    def test_suite_and_campaign_commands_are_deterministic(self) -> None:
        code, result, _ = invoke([
            "suite", "validate", "--input", str(FIXTURES / "cases.json"),
            "--input", str(FIXTURES / "rubrics.json"),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(result["counts"], {"Case": 5, "Rubric": 5})

        spec_path = self.root / "campaign.json"
        write(spec_path, e.campaign_encode(make_spec(repetitions=1)))
        first = invoke(["campaign", "compile", "--input", str(spec_path)])
        second = invoke(["campaign", "compile", "--input", str(spec_path)])
        self.assertEqual(first, second)
        resolved = e.campaign_decode(json.dumps(first[1]))
        self.assertEqual(len(resolved.trials), 6)

    def _publication_fixture(self) -> tuple[Path, Path, Path, e.ObjectStore, e.ResolvedCampaign]:
        store_path = self.root / "store"
        store_path.mkdir()
        store = e.ObjectStore(store_path)
        campaign = e.compile_campaign(make_spec(repetitions=1))
        campaign_path = self.root / "resolved.json"
        write(campaign_path, e.campaign_encode(campaign))
        entries = []
        for trial in campaign.trials:
            evidence_ref = store.put_record(e.AdmittedEvidence("response", ("output",)))
            execution_ref = store.put_record(e.ExecutionResultImport(
                e.digest(campaign), trial.trial_id, trial.requested_profile_sha256,
                trial.requested_profile_sha256, "complete", evidence_ref,
            ))
            entries.append(e.ResultSetEntry(
                trial.trial_id, execution_ref, evidence_ref,
                "usable", "present", "verified",
            ))
        entries_path = self.root / "entries.json"
        write(entries_path, [asdict(item) for item in entries])
        return store_path, campaign_path, entries_path, store, campaign

    def test_result_manifest_and_analysis_commands_replay_bindings(self) -> None:
        store_path, campaign_path, entries_path, store, campaign = self._publication_fixture()
        seal_arguments = [
            "result-set", "seal", "--store", str(store_path),
            "--campaign", str(campaign_path), "--entries", str(entries_path),
        ]
        code, sealed, _ = invoke(seal_arguments)
        self.assertEqual(code, 0)
        self.assertEqual(invoke(seal_arguments)[1]["ref"], sealed["ref"])
        sealed_ref = sealed["ref"]
        sealed_path = self.root / "sealed-ref.json"
        write(sealed_path, sealed_ref)
        validated = invoke([
            "result-set", "validate", "--store", str(store_path),
            "--campaign", str(campaign_path), "--ref", str(sealed_path),
        ])[1]
        self.assertEqual(validated["entries"], len(campaign.trials))

        manifest = invoke([
            "manifest", "write", "--store", str(store_path),
            "--campaign", str(campaign_path), "--result-set", str(sealed_path),
            "--retention-policy-sha256", "f" * 64,
        ])[1]
        manifest_ref = e.ContentRef(**manifest["ref"])
        manifest_path = self.root / "manifest.json"
        write(manifest_path, store.read(manifest_ref).decode())
        self.assertEqual(invoke(["manifest", "validate", "--input", str(manifest_path)])[0], 0)

        spec_path = self.root / "analysis-spec.json"
        write(spec_path, e.publication_encode(e.AnalysisSpec("rubric", "instructions", 0.0, 1.0)))
        packet_arguments = [
            "analysis", "packet", "--store", str(store_path),
            "--campaign", str(campaign_path), "--result-set", str(sealed_path),
            "--spec", str(spec_path),
        ]
        packet = invoke(packet_arguments)[1]
        self.assertEqual(invoke(packet_arguments)[1], packet)
        packet_ref = e.ContentRef(**packet["packet_ref"])
        mapping_ref = e.ContentRef(**packet["mapping_ref"])
        mapping_path = self.root / "mapping-ref.json"
        write(mapping_path, packet["mapping_ref"])
        packet_record = store.read_record(packet_ref)
        result = e.AnalysisResult(
            packet_ref, mapping_ref.digest, "a" * 64, "a" * 64, "complete",
            tuple(e.AnalysisMeasure(item.label, 0.5) for item in packet_record.items),
            1, None,
        )
        result_path = self.root / "analysis-result.json"
        write(result_path, e.publication_encode(result))
        validated = invoke([
            "analysis", "validate", "--input", str(result_path),
            "--store", str(store_path), "--campaign", str(campaign_path),
            "--mapping", str(mapping_path),
        ])[1]
        self.assertEqual(validated["bindings"], "verified")
        result_ref = e.import_analysis_result(store, result, mapping_ref, campaign)
        result_ref_path = self.root / "result-ref.json"
        write(result_ref_path, asdict(result_ref))
        unblinded = invoke([
            "analysis", "unblind", "--store", str(store_path),
            "--campaign", str(campaign_path), "--result", str(result_ref_path),
            "--mapping", str(mapping_path),
        ])[1]
        self.assertIsInstance(store.read_record(e.ContentRef(**unblinded["ref"])), e.UnblindedComparison)

    def test_grade_rescore_compare_and_valid_outcome_exit_zero(self) -> None:
        case = load_catalog("cases.json", e.Case)[-1]
        rubric = load_catalog("rubrics.json", e.Rubric)[-1]
        runs = tuple(make_run(case, arm) for arm in e.ARMS)
        scores = tuple(e.score_run(case, run, rubric) for run in runs)
        paths = {}
        for name, record in (("case", case), ("variant", VARIANT), ("rubric", rubric)):
            paths[name] = self.root / f"{name}.json"
            write(paths[name], e.encode(record))
        run_paths, score_paths = [], []
        for index, (run, score) in enumerate(zip(runs, scores)):
            run_path, score_path = self.root / f"run-{index}.json", self.root / f"score-{index}.json"
            write(run_path, e.encode(run))
            write(score_path, e.encode(score))
            run_paths.append(run_path)
            score_paths.append(score_path)

        for command in ("grade", "rescore"):
            code, result, _ = invoke([
                command, "--case", str(paths["case"]), "--run", str(run_paths[2]),
                "--rubric", str(paths["rubric"]),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(result["data"]["verdict"], "failed")
        arguments = [
            "compare", "--case", str(paths["case"]), "--variant", str(paths["variant"]),
            "--rubric", str(paths["rubric"]),
        ]
        for path in run_paths:
            arguments += ["--run", str(path)]
        for path in score_paths:
            arguments += ["--score", str(path)]
        code, result, _ = invoke(arguments)
        self.assertEqual((code, result["efficacy"]), (0, "inconclusive"))

    def test_malformed_inputs_are_sanitized_closed_json(self) -> None:
        sensitive = self.root / "private-name.json"
        write(sensitive, "not json")
        code, result, stderr = invoke(["parse", str(sensitive)])
        self.assertEqual((code, result["schema"], result["status"]), (2, CLI_SCHEMA, "invalid"))
        self.assertNotIn(str(sensitive), stderr)
        self.assertNotIn("not json", stderr)

    def test_portable_modules_do_not_import_workspace_execution_or_connector_code(self) -> None:
        forbidden = {"codex", "workspace", "execution", "connector"}
        for path in (ROOT / "src" / "agent_evals").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                self.assertFalse(forbidden & {name.split(".")[0] for name in names}, path)


if __name__ == "__main__":
    unittest.main()

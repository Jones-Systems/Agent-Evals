"""Small lexical fixtures prove publication and analysis binding boundaries."""

from dataclasses import asdict, replace
from importlib import resources
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

import agent_evals as e
from agent_evals.protocol.publication import RECORD_TYPES
from agent_evals.protocol.analysis import ANALYSIS_TYPES
from test_campaign import make_spec


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = e.ObjectStore(self.temp.name)
        self.campaign = e.compile_campaign(make_spec(repetitions=1))

    def entries(self, *, missing=False, observed=True, completion="complete"):
        entries = []
        for trial in self.campaign.trials:
            evidence_ref = None if missing else self.store.put_record(e.AdmittedEvidence("same response", ("same output",)))
            execution_ref = self.store.put_record(e.ExecutionResultImport(
                e.digest(self.campaign), trial.trial_id, trial.requested_profile_sha256,
                trial.requested_profile_sha256 if observed else None, completion, evidence_ref,
            ))
            entries.append(e.ResultSetEntry(trial.trial_id, execution_ref, evidence_ref,
                "usable" if not missing and observed and completion == "complete" else "incomplete",
                "missing" if missing else "present", "missing" if missing else "verified"))
        return tuple(entries)

    def sealed(self, **kwargs):
        return e.seal_result_set(self.store, self.campaign, self.entries(**kwargs))

    def analysis(self, *, missing=False):
        ref = self.sealed(missing=missing)
        packet_ref, mapping_ref = e.blind_result_set(self.store, ref, self.campaign, e.AnalysisSpec("quality rubric", "grade admitted output", 0.0, 1.0))
        packet = self.store.read_record(packet_ref)
        result = e.AnalysisResult(packet_ref, mapping_ref.digest, "a" * 64, "b" * 64, "complete",
            tuple(e.AnalysisMeasure(item.label, None if item.evidence is None else 0.5) for item in packet.items), 1, None)
        return packet_ref, mapping_ref, result

    def test_sealing_canonical_and_immutable_revision(self):
        entries = self.entries()
        first = e.seal_result_set(self.store, self.campaign, entries)
        self.assertEqual(first, e.seal_result_set(self.store, self.campaign, tuple(reversed(entries))))
        old_bytes = self.store.read(first)
        second = e.seal_result_set(self.store, self.campaign, entries, predecessor=first)
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.read_record(second).predecessor, first)
        self.assertEqual(self.store.read(first), old_bytes)

    def test_sealing_rejects_dangling_size_duplicate_and_trial_bindings(self):
        entries = self.entries()
        failures = [
            entries + entries[:1], entries[1:],
            (replace(entries[0], trial_id="unknown"),) + entries[1:],
            (replace(entries[0], execution_ref=entries[1].execution_ref),) + entries[1:],
            (replace(entries[0], evidence_ref=replace(entries[0].evidence_ref, digest="0" * 64)),) + entries[1:],
            (replace(entries[0], execution_ref=replace(entries[0].execution_ref, byte_size=1)),) + entries[1:],
        ]
        for values in failures:
            with self.subTest(values=values), self.assertRaises(e.EvaluationError):
                e.seal_result_set(self.store, self.campaign, values)
        missing_ref = replace(entries[0].evidence_ref, digest="0" * 64)
        execution = replace(self.store.read_record(entries[0].execution_ref), evidence_ref=missing_ref)
        bad_entry = replace(entries[0], execution_ref=self.store.put_record(execution), evidence_ref=missing_ref)
        with self.assertRaises(e.EvaluationError):
            e.seal_result_set(self.store, self.campaign, (bad_entry,) + entries[1:])

    def test_wrong_campaign_requested_binding_and_state_separation(self):
        entries = self.entries()
        original = self.store.read_record(entries[0].execution_ref)
        for bad in (replace(original, campaign_sha256="0" * 64), replace(original, requested_profile_sha256="0" * 64)):
            ref = self.store.put_record(bad)
            with self.assertRaises(e.EvaluationError):
                e.seal_result_set(self.store, self.campaign, (replace(entries[0], execution_ref=ref),) + entries[1:])
        for kwargs in ({"missing": True}, {"observed": False}, {"completion": "partial"}):
            entries = self.entries(**kwargs)
            sealed = e.seal_result_set(self.store, self.campaign, entries)
            self.assertIsInstance(self.store.read_record(sealed), e.SealedResultSet)
            with self.assertRaises(e.EvaluationError):
                e.seal_result_set(self.store, self.campaign, tuple(replace(entry, classification="usable") for entry in entries))
        absent = tuple(e.ResultSetEntry(trial.trial_id, None, None, "unavailable", "missing", "missing") for trial in self.campaign.trials)
        self.assertIsInstance(self.store.read_record(e.seal_result_set(self.store, self.campaign, absent)), e.SealedResultSet)

    def test_public_manifest_is_closed_and_contains_no_evidence_or_identifiers(self):
        ref = self.sealed()
        manifest_ref = e.publish_manifest(self.store, ref, self.campaign, retention_policy_sha256="f" * 64)
        manifest = self.store.read_record(manifest_ref)
        text = self.store.read(manifest_ref).decode()
        self.assertNotIn("same response", text)
        self.assertNotIn(self.campaign.trials[0].trial_id, text)
        self.assertTrue(all(self.store.read_record(e.ContentRef(**asdict(item))) == e.PublicSummary("usable", "present") for item in manifest.summaries))
        schema = e.publication_json_schema(e.PublishedResultManifest)
        self.assertEqual(set(schema["$defs"]), {"PublishedResultManifest", "PublicContentRef"})
        self.assertEqual(set(schema["$defs"]["PublishedResultManifest"]["properties"]), {"result_set_sha256", "summaries", "redaction_revision", "access_class", "retention_policy_sha256"})
        for definition in schema["$defs"].values():
            self.assertFalse(definition["additionalProperties"])
            for prop in definition["properties"].values():
                if prop.get("type") == "string":
                    self.assertEqual(prop["pattern"], "^[0-9a-f]{64}$")
        for name in ("prompt", "response", "trace", "private_path", "workspace", "credential", "account", "conversation", "browser", "session"):
            raw = json.loads(text)
            raw["data"][name] = "forbidden"
            with self.assertRaises(e.EvaluationError):
                e.publication_decode(json.dumps(raw))

    def test_deterministic_packets_equal_evidence_independent_of_arm_metadata(self):
        ref = self.sealed()
        spec = e.AnalysisSpec("rubric", "instructions", 0, 1)
        packet_ref, mapping_ref = e.blind_result_set(self.store, ref, self.campaign, spec)
        self.assertEqual((packet_ref, mapping_ref), e.blind_result_set(self.store, ref, self.campaign, spec))
        text = self.store.read(packet_ref).decode()
        for field in ("trial_id", "arm_id", "variant", "runtime", "workspace", "result_set", "mapping"):
            self.assertNotIn(field, text)
        old_campaign = self.campaign
        spec2 = replace(old_campaign.campaign_spec, campaign_id="different-campaign", arms=tuple("renamed-" + arm for arm in old_campaign.campaign_spec.arms), profile_matrix=tuple(replace(item, arm_id="renamed-" + item.arm_id) for item in old_campaign.campaign_spec.profile_matrix))
        self.campaign = e.compile_campaign(spec2)
        other_packet, other_mapping = e.blind_result_set(self.store, self.sealed(), self.campaign, spec)
        self.assertEqual(packet_ref, other_packet)
        self.assertNotEqual(mapping_ref, other_mapping)

    def test_unblind_requires_persisted_terminal_result_and_mapping(self):
        packet_ref, mapping_ref, result = self.analysis()
        pending = e.import_analysis_result(self.store, replace(result, status="pending"), mapping_ref, self.campaign)
        with self.assertRaises(e.EvaluationError):
            e.unblind(self.store, pending, mapping_ref, self.campaign)
        with self.assertRaises(e.EvaluationError):
            e.unblind(self.store, replace(pending, digest="0" * 64), mapping_ref, self.campaign)
        with self.assertRaises(e.EvaluationError):
            e.import_analysis_result(self.store, replace(result, mapping_sha256="0" * 64), mapping_ref, self.campaign)
        mapping = self.store.read_record(mapping_ref)
        forged = self.store.put_record(replace(mapping, entries=tuple(replace(item, arm_id="wrong-arm") for item in mapping.entries)))
        with self.assertRaises(e.EvaluationError):
            e.import_analysis_result(self.store, replace(result, mapping_sha256=forged.digest), forged, self.campaign)
        result_ref = e.import_analysis_result(self.store, result, mapping_ref, self.campaign)
        comparison_ref = e.unblind(self.store, result_ref, mapping_ref, self.campaign)
        comparison = self.store.read_record(comparison_ref)
        self.assertEqual(comparison.analysis_result_ref, result_ref)
        self.assertEqual({item.trial_id for item in comparison.measures}, {trial.trial_id for trial in self.campaign.trials})
        self.assertEqual(self.store.read_record(result_ref).requested_profile_sha256, "a" * 64)
        self.assertEqual(self.store.read_record(result_ref).observed_profile_sha256, "b" * 64)

    def test_missingness_null_and_immutable_analysis_derivation(self):
        _, mapping_ref, result = self.analysis(missing=True)
        first = e.import_analysis_result(self.store, result, mapping_ref, self.campaign)
        comparison = self.store.read_record(e.unblind(self.store, first, mapping_ref, self.campaign))
        self.assertTrue(all(item.value is None for item in comparison.measures))
        old = self.store.read(first)
        second = e.import_analysis_result(self.store, replace(result, revision=2, predecessor=first, status="inconclusive"), mapping_ref, self.campaign)
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.read(first), old)
        with self.assertRaises(e.EvaluationError):
            e.import_analysis_result(self.store, replace(result, revision=3, predecessor=first), mapping_ref, self.campaign)
        with self.assertRaises(e.EvaluationError):
            e.import_analysis_result(self.store, replace(result, measures=tuple(replace(item, value=0.0) for item in result.measures)), mapping_ref, self.campaign)

    def test_packaged_schemas_and_roundtrip(self):
        for record_type in RECORD_TYPES | ANALYSIS_TYPES:
            expected = e.publication_json_schema(record_type)
            name = expected["title"].split("/")[0].split(".")[-1] + "-v1.schema.json"
            actual = json.loads(resources.files("agent_evals").joinpath("resources", name).read_text())
            self.assertEqual(actual, expected)
        ref = self.sealed()
        value = self.store.read_record(ref)
        self.assertEqual(e.publication_decode(e.publication_encode(value)), value)
        with self.assertRaises(e.EvaluationError):
            e.publication_decode('{"schema":"x","schema":"y"}')

    def test_public_references_are_directly_readable(self):
        ref = e.publish_manifest(self.store, self.sealed(), self.campaign, retention_policy_sha256="f" * 64)
        public_ref = self.store.read_record(ref).summaries[0]
        self.assertEqual(self.store.read_record(public_ref), e.PublicSummary("usable", "present"))

    def test_nullable_digest_schema_has_exact_hash_branch(self):
        for record_type in (e.ExecutionResultImport, e.AnalysisResult):
            prop = e.publication_json_schema(record_type)["$defs"][record_type.__name__]["properties"]["observed_profile_sha256"]
            self.assertIn({"type": "null"}, prop["anyOf"])
            string = next(branch for branch in prop["anyOf"] if branch.get("type") == "string")
            self.assertEqual(string.get("pattern"), "^[0-9a-f]{64}$")
            self.assertEqual((string["minLength"], string["maxLength"]), (64, 64))

    def test_all_result_predecessors_are_verified(self):
        first = self.store.read_record(self.sealed())
        bad_entry = replace(first.entries[0], execution_ref=replace(first.entries[0].execution_ref, digest="0" * 64))
        forged_first = self.store.put_record(replace(first, entries=(bad_entry,) + first.entries[1:]))
        forged_second = self.store.put_record(replace(first, revision=2, predecessor=forged_first))
        with self.assertRaises(e.EvaluationError):
            e.seal_result_set(self.store, self.campaign, first.entries, predecessor=forged_second)

    def test_all_analysis_predecessors_are_verified(self):
        _, mapping_ref, result = self.analysis()
        bad = replace(result, measures=tuple(replace(item, value=99.0) for item in result.measures))
        forged_first = self.store.put_record(bad)
        forged_second = self.store.put_record(replace(result, revision=2, predecessor=forged_first))
        with self.assertRaises(e.EvaluationError):
            e.import_analysis_result(self.store, replace(result, revision=3, predecessor=forged_second), mapping_ref, self.campaign)

    def test_revision_limits_are_explicit_in_schema_and_runtime(self):
        result_set_ref = self.sealed()
        result_set = self.store.read_record(result_set_ref)
        with self.assertRaises(e.EvaluationError):
            self.store.put_record(replace(result_set, revision=65, predecessor=result_set_ref))
        for record_type in (e.SealedResultSet, e.AnalysisResult):
            prop = e.publication_json_schema(record_type)["$defs"][record_type.__name__]["properties"]["revision"]
            self.assertEqual((prop["minimum"], prop["maximum"]), (1, 64))

    def test_check_group_catalog_covers_discovered_modules(self):
        tests = Path(__file__).resolve().parent
        catalog = tomllib.loads((tests / "check-groups.toml").read_text())
        self.assertEqual(catalog["schema"], "agent-evals-check-groups/v1")
        modules = {module for group in catalog["groups"].values() for module in group["modules"]}
        self.assertEqual(modules, {path.stem for path in tests.glob("test_*.py")})


if __name__ == "__main__":
    unittest.main()

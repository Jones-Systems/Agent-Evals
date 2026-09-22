"""Bounded atomic and content-integrity tests for the local object store."""

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

import agent_evals as e
import agent_evals.publication.object_store as object_store_module


PRIVATE_SCHEMA = "codex.agent-evals-analysis-spec/v1"
PUBLIC_SCHEMA = "codex.agent-evals-public-summary/v1"


def private_record() -> e.AnalysisSpec:
    return e.AnalysisSpec("quality-rubric", "grade admitted output", 0.0, 1.0)


class ObjectStoreTests(unittest.TestCase):
    def test_record_round_trip_and_access_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            private = private_record()
            private_ref = store.put_record(private)
            self.assertEqual(private_ref.media_type, "application/json")
            self.assertEqual(private_ref.record_schema, PRIVATE_SCHEMA)
            self.assertEqual(private_ref.access_class, "private")
            self.assertEqual(store.read_record(private_ref), private)

            public = e.PublicSummary("usable", "present")
            public_ref = store.put_record(public, access_class="public")
            self.assertEqual(public_ref.record_schema, PUBLIC_SCHEMA)
            self.assertEqual(public_ref.access_class, "public")
            self.assertEqual(store.read_record(public_ref), public)

    def test_raw_admission_is_closed_json_and_leaves_no_file_on_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            encoded = e.publication_encode(private_record()).encode("utf-8")
            attempts = (
                (b"plain", "text/plain", PRIVATE_SCHEMA, "private"),
                (b"{}", "application/json", PRIVATE_SCHEMA, "private"),
                (encoded, "application/json", PUBLIC_SCHEMA, "public"),
                (encoded, "application/json", PRIVATE_SCHEMA, "public"),
                (b"x" * (e.MAX_BYTES + 1), "application/json", PRIVATE_SCHEMA, "private"),
            )
            for data, media_type, schema, access_class in attempts:
                with self.subTest(media_type=media_type, schema=schema, access_class=access_class):
                    with self.assertRaises(e.EvaluationError):
                        store.put(
                            data,
                            media_type=media_type,
                            record_schema=schema,
                            access_class=access_class,
                        )
            self.assertEqual(tuple(store.root.iterdir()), ())

    def test_idempotence_collision_and_reference_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            data = e.publication_encode(private_record()).encode("utf-8")
            ref = store.put(
                data,
                media_type="application/json",
                record_schema=PRIVATE_SCHEMA,
                access_class="private",
            )
            self.assertEqual(store.put(data, media_type=ref.media_type, record_schema=ref.record_schema, access_class=ref.access_class), ref)

            collision = b"collision"
            final = store.root / ref.digest
            final.write_bytes(collision)
            with self.assertRaises(e.EvaluationError):
                store.put(
                    data,
                    media_type="application/json",
                    record_schema=PRIVATE_SCHEMA,
                    access_class="private",
                )
            with self.assertRaises(e.EvaluationError):
                store.read(ref)
            with self.assertRaises(e.EvaluationError):
                store.read(replace(ref, byte_size=len(collision)))

            forged_digest = replace(ref, digest=hashlib.sha256(collision).hexdigest())
            with self.assertRaises(e.EvaluationError):
                store.read(forged_digest)

    def test_safe_reads_reject_symlink_and_nonregular_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            data = e.publication_encode(private_record()).encode("utf-8")
            ref = store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")
            final = store.root / ref.digest
            final.unlink()
            target = store.root / "target"
            target.write_bytes(data)
            final.symlink_to(target)
            with self.assertRaises(e.EvaluationError):
                store.read(ref)

            final.unlink()
            final.mkdir()
            with self.assertRaises(e.EvaluationError):
                store.read(ref)

    def test_missing_object_is_sanitized_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            ref = e.ContentRef("0" * 64, 0, "application/json", PRIVATE_SCHEMA, "private")
            with self.assertRaisesRegex(e.EvaluationError, "object unavailable"):
                store.read(ref)

    def test_staging_link_fsync_and_baseexception_failures_clean_exact_stage(self) -> None:
        data = e.publication_encode(private_record()).encode("utf-8")
        cases = (
            ("staging", patch.object(object_store_module.tempfile, "mkstemp", side_effect=OSError("stage"))),
            ("write", patch.object(object_store_module.os, "fdopen", side_effect=OSError("write"))),
            ("link", patch.object(object_store_module.os, "link", side_effect=OSError("link"))),
            ("interrupt", patch.object(object_store_module.os, "link", side_effect=KeyboardInterrupt())),
        )
        for label, failure in cases:
            with self.subTest(failure=label), tempfile.TemporaryDirectory() as temporary:
                store = e.ObjectStore(Path(temporary) / "objects")
                with failure:
                    expected = KeyboardInterrupt if label == "interrupt" else OSError
                    with self.assertRaises(expected):
                        store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")
                self.assertEqual(tuple(store.root.iterdir()), ())

        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            with patch.object(object_store_module.os, "fsync", side_effect=OSError("file")):
                with self.assertRaisesRegex(OSError, "file"):
                    store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")
            self.assertEqual(tuple(store.root.iterdir()), ())

        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            with patch.object(object_store_module.os, "fsync", side_effect=[None, OSError("directory")]):
                with self.assertRaises(OSError):
                    store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")
            self.assertEqual(tuple(path for path in store.root.iterdir() if path.name.endswith(".tmp")), ())
            self.assertEqual(store.read_record(store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")), e.publication_decode(data.decode("utf-8")))

        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            with patch.object(object_store_module.os, "link", side_effect=KeyboardInterrupt("primary")), patch.object(object_store_module.os, "unlink", side_effect=OSError("cleanup")):
                with self.assertRaisesRegex(KeyboardInterrupt, "primary"):
                    store.put(data, media_type="application/json", record_schema=PRIVATE_SCHEMA, access_class="private")
            self.assertEqual(len(tuple(path for path in store.root.iterdir() if path.name.endswith(".tmp"))), 1)

    def test_concurrent_publishers_converge_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = e.ObjectStore(Path(temporary) / "objects")
            value = private_record()
            start = threading.Barrier(8)

            def publish() -> e.ContentRef:
                start.wait()
                return store.put_record(value)

            with ThreadPoolExecutor(max_workers=8) as pool:
                refs = tuple(pool.map(lambda _: publish(), range(8)))
            self.assertEqual(set(refs), {refs[0]})
            self.assertEqual(tuple(path for path in store.root.iterdir() if not path.name.startswith(".")), (store.root / refs[0].digest,))
            self.assertEqual(tuple(path for path in store.root.iterdir() if path.name.endswith(".tmp")), ())


if __name__ == "__main__":
    unittest.main()

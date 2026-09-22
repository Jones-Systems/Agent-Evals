"""Immutable, content-addressed storage for closed publication records.

``ObjectStore`` uses a local directory trusted by its caller.  The store does
not establish ownership, permissions, or isolation for that directory and does
not protect against another trusted writer modifying it.  Object names are
validated content digests; staging and final objects are kept in that same
directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from agent_evals.core import EvaluationError, MAX_BYTES, _pairs
from agent_evals.protocol.publication import (
    ContentRef,
    publication_decode,
    publication_encode,
    validate_content_ref,
)


_JSON_MEDIA_TYPE = "application/json"
_PUBLIC_RECORD_SCHEMAS = frozenset(
    {
        "codex.agent-evals-public-summary/v1",
        "codex.agent-evals-public-manifest/v1",
    }
)
_STAGE_SUFFIX = ".tmp"


def _fail(condition: bool, reason: str) -> None:
    if not condition:
        raise EvaluationError(reason)


def _parse_envelope(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                EvaluationError("nonfinite JSON")
            ),
        )
    except EvaluationError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise EvaluationError("malformed publication record") from exc
    _fail(type(raw) is dict and set(raw) == {"schema", "kind", "data"}, "unknown envelope")
    _fail(type(raw["schema"]) is str, "invalid publication schema")
    return raw


def _validate_publication_bytes(data: bytes, record_schema: str) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationError("invalid publication text") from exc
    envelope = _parse_envelope(text)
    _fail(envelope["schema"] == record_schema, "content schema mismatch")
    # The closed decoder performs the complete schema and semantic validation.
    publication_decode(text)


def _read_fd_bounded(fd: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, MAX_BYTES + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        _fail(total <= MAX_BYTES, "object byte bound")
        if total >= expected_size:
            # A subsequent read distinguishes an exact-size file from a file
            # which grew after its initial stat.
            continue
    return b"".join(chunks)


class ObjectStore:
    """Store immutable closed records below one caller-trusted local root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            supplied = Path(root)
        except (TypeError, ValueError) as exc:
            raise EvaluationError("invalid object store root") from exc
        try:
            if supplied.is_symlink():
                raise EvaluationError("object store root unavailable")
            supplied.mkdir(parents=True, exist_ok=True)
            _fail(supplied.is_dir(), "object store root unavailable")
        except EvaluationError:
            raise
        except OSError as exc:
            raise EvaluationError("object store root unavailable") from exc
        self.root = supplied.absolute()

    @staticmethod
    def _validate_ref_metadata(ref: ContentRef) -> None:
        validate_content_ref(ref)
        _fail(ref.media_type == _JSON_MEDIA_TYPE, "unsupported object media type")
        expected_access = "public" if ref.record_schema in _PUBLIC_RECORD_SCHEMAS else "private"
        _fail(ref.access_class == expected_access, "invalid object access class")

    def _root_fd(self) -> int:
        _fail(hasattr(os, "O_NOFOLLOW"), "safe object store unavailable")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        return os.open(self.root, flags)

    def _fsync_root(self) -> None:
        directory_fd = self._root_fd()
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _read_named(self, name: str) -> bytes:
        path = self.root / name
        try:
            before_path = os.lstat(path)
        except FileNotFoundError:
            raise
        except OSError:
            raise
        _fail(stat.S_ISREG(before_path.st_mode), "unsafe object file")
        _fail(before_path.st_size <= MAX_BYTES, "object byte bound")
        _fail(hasattr(os, "O_NOFOLLOW"), "safe object reader unavailable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            _fail(stat.S_ISREG(before.st_mode), "unsafe object file")
            _fail(before.st_size <= MAX_BYTES, "object byte bound")
            raw = _read_fd_bounded(descriptor, before.st_size)
            after = os.fstat(descriptor)
            _fail(stat.S_ISREG(after.st_mode), "unsafe object file")
            _fail(
                (before.st_dev, before.st_ino, before.st_size)
                == (after.st_dev, after.st_ino, after.st_size)
                == (before.st_dev, before.st_ino, len(raw)),
                "object changed while reading",
            )
            return raw
        finally:
            os.close(descriptor)

    def _validate_existing(self, ref: ContentRef, data: bytes) -> None:
        existing = self._read_named(ref.digest)
        _fail(len(existing) == ref.byte_size, "object size mismatch")
        _fail(hashlib.sha256(existing).hexdigest() == ref.digest, "object digest mismatch")
        _fail(existing == data, "digest collision or content mismatch")

    @staticmethod
    def _cleanup_stage(path: str) -> BaseException | None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return None
        except BaseException as exc:
            return exc
        return None

    def _publish_new(self, ref: ContentRef, data: bytes) -> ContentRef:
        final_path = self.root / ref.digest
        stage_path: str | None = None
        primary: BaseException | None = None
        result: ContentRef | None = None
        finalized = False
        try:
            descriptor, stage_path = tempfile.mkstemp(
                prefix=f".{ref.digest}.", suffix=_STAGE_SUFFIX, dir=self.root
            )
            raw_descriptor: int | None = descriptor
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    raw_descriptor = None
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if raw_descriptor is not None:
                    os.close(raw_descriptor)

            try:
                os.link(stage_path, final_path, follow_symlinks=False)
            except FileExistsError:
                self._validate_existing(ref, data)
                result = ref
                finalized = True
            else:
                # The hard link is the only finalization operation.  It never
                # replaces an existing name, so concurrent publishers converge
                # on one immutable object or fail closed on a collision.
                result = ref
                finalized = True
        except BaseException as exc:
            primary = exc

        cleanup_error = self._cleanup_stage(stage_path) if stage_path is not None else None
        if primary is not None:
            if cleanup_error is not None:
                primary.add_note("temporary object cleanup failed: " + repr(cleanup_error))
            raise primary
        if cleanup_error is not None:
            raise cleanup_error
        _fail(result is not None, "object publication did not produce a reference")
        if finalized:
            # Sync after removing the stage so a successful return durably
            # records both the immutable final name and exact temp cleanup.
            self._fsync_root()
        return result

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        record_schema: str,
        access_class: str,
    ) -> ContentRef:
        """Validate and immutably admit one closed JSON publication record."""
        _fail(type(data) is bytes, "object content must be bytes")
        _fail(len(data) <= MAX_BYTES, "object byte bound")
        digest = hashlib.sha256(data).hexdigest()
        ref = ContentRef(digest, len(data), media_type, record_schema, access_class)
        self._validate_ref_metadata(ref)
        _validate_publication_bytes(data, ref.record_schema)

        try:
            self._validate_existing(ref, data)
        except FileNotFoundError:
            pass
        else:
            # An idempotent caller may race the publisher that created this
            # name; establish the parent-directory durability claim here too.
            self._fsync_root()
            return ref
        return self._publish_new(ref, data)

    def read(self, ref: ContentRef) -> bytes:
        """Read one bounded immutable object without following final symlinks."""
        self._validate_ref_metadata(ref)
        try:
            data = self._read_named(ref.digest)
        except FileNotFoundError as exc:
            raise EvaluationError("object unavailable") from exc
        _fail(len(data) == ref.byte_size, "object size mismatch")
        _fail(hashlib.sha256(data).hexdigest() == ref.digest, "object digest mismatch")
        return data

    def put_record(self, value: object, *, access_class: str = "private") -> ContentRef:
        encoded = publication_encode(value)
        try:
            data = encoded.encode("utf-8")
        except UnicodeError as exc:
            raise EvaluationError("invalid publication text") from exc
        envelope = _parse_envelope(encoded)
        record_schema = envelope["schema"]
        return self.put(
            data,
            media_type=_JSON_MEDIA_TYPE,
            record_schema=record_schema,
            access_class=access_class,
        )

    def read_record(self, ref: ContentRef) -> object:
        self._validate_ref_metadata(ref)
        data = self.read(ref)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvaluationError("invalid publication text") from exc
        envelope = _parse_envelope(text)
        _fail(envelope["schema"] == ref.record_schema, "content schema mismatch")
        return publication_decode(text)

"""Immutable local result storage and explicit public projections."""

from .object_store import ObjectStore
from .result_set import seal_result_set, verify_result_set
from .public_manifest import publish_manifest

__all__ = ["ObjectStore", "seal_result_set", "verify_result_set", "publish_manifest"]

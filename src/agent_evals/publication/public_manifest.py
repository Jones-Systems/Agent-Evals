"""Public projection copies only closed summary fields, never evidence text."""

from dataclasses import asdict

from agent_evals.protocol.publication import PublicContentRef, PublishedResultManifest, PublicSummary, hash_field
from .result_set import verify_result_set


def publish_manifest(store, result_set_ref, campaign, *, retention_policy_sha256: str):
    hash_field(retention_policy_sha256)
    result_set = verify_result_set(store, result_set_ref, campaign)
    summaries = tuple(
        PublicContentRef(**asdict(store.put_record(PublicSummary(entry.classification, entry.missingness), access_class="public")))
        for entry in result_set.entries
    )
    manifest = PublishedResultManifest(result_set_ref.digest, summaries, "closed-projection-v1", "public", retention_policy_sha256)
    return store.put_record(manifest, access_class="public")

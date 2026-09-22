# Agent-Evals

Agent-Evals provides a workspace-independent, standard-library-first Python
core for validating, hashing, grading, and comparing bounded agent-evaluation
records. It also compiles closed campaign specifications into deterministic,
workspace-independent trial plans. Each layer has an independent wire contract.

## Install and use

The package requires Python 3.11 or later and has no runtime dependencies.

    python -m pip install .
    agent-evals schema
    agent-evals parse record.json
    agent-evals grade --case case.json --run run.json --rubric rubric.json

parse accepts one closed record envelope. The fixture catalogs in tests/ are
JSON arrays, so applications should decode each catalog entry individually.
grade and its compatibility alias rescore operate only on already-preserved
evidence; they never run an agent.

Library callers can use the same operations directly:

    from agent_evals import decode, digest, encode, score_run
    record = decode(record_json)
    content_id = digest(record)
    normalized_json = encode(record)

Campaign callers construct a `CampaignSpec` and call `compile_campaign`. The
result contains stable trial identities and requested execution profiles only;
it does not create jobs, attempts, workspaces, or runtime observations.

## Architecture and status

The portable core owns closed dataclasses, semantic validation, canonical SHA-256
content hashes, deterministic checks and measures, blinded judge packets,
human-review bindings, rescoring, matched-triplet comparison, and the packaged
JSON Schema. The implementation uses only the Python standard library at
runtime.

The campaign compiler adds only immutable allocation and ordering. It
deliberately does not create or capture workspaces, invoke a provider or agent
runtime, import connector/browser code, decrement budgets, retry execution,
analyze or publish results, or qualify serving behavior. Hashes establish content equality, not
observation authenticity, safety, or authorization. See
[the protocol](docs/contracts/evaluation-protocol.md) and
[the roadmap](docs/roadmap.md) for the boundary.

The package is alpha software. codex.skill-evaluation/v1 compatibility is
covered by transferred fixtures and parity tests, while future portable
protocol layers will receive their own versioned identities.

## Development

    PYTHONPATH=src python -m unittest discover -s tests -v
    uv build

The consumer-owned CI caller tests Python 3.11, 3.12, and 3.13, builds both
distribution formats, and imports the installed wheel.

# Agent-Evals

Agent-Evals provides a workspace-independent, standard-library-first Python
core for validating, hashing, grading, and comparing bounded agent-evaluation
records. It also compiles closed campaign specifications into deterministic,
workspace-independent trial plans. Each layer has an independent wire contract.
An immutable local object store seals imported results, emits closed public
summaries, and prepares neutral-label analysis packets without executing models.

## Install and use

The package requires Python 3.11 or later and has no runtime dependencies.

    python -m pip install .
    agent-evals schema list
    agent-evals schema show --name codex.skill-evaluation/v1
    agent-evals capabilities
    agent-evals suite validate --input cases.json --input rubrics.json
    agent-evals campaign compile --input campaign.json
    agent-evals grade --case case.json --run run.json --rubric rubric.json
    agent-evals conformance

Every invocation emits exactly one closed JSON document on stdout. Completed
commands use exit 0, including valid failed or inconclusive evaluations.
Malformed or binding-invalid inputs use exit 2; unavailable workspace or live
execution capabilities use exit 3. Diagnostics contain only fixed reason codes,
not input text, exceptions, or selected paths.

`suite validate` accepts one or more explicit `--input` files containing a
closed record or a JSON array of closed records. `grade` and `rescore` operate
only on already-preserved evidence. `compare` requires three explicit `--run`
and three explicit `--score` selectors. The legacy `schema` and `parse`
commands remain available for compatibility.

The publication commands require an existing explicit local object-store root,
a resolved campaign file, and content-reference selector files. A selector is
the closed JSON object containing `digest`, `byte_size`, `media_type`,
`record_schema`, and `access_class`. `result-set seal` additionally consumes an
explicit JSON array of closed result-set entry objects. `manifest write`,
`analysis packet`, and `analysis unblind` write only immutable records to that
selected local store. They do not publish remotely or execute analysis.

Library callers can use the same operations directly:

    from agent_evals import decode, digest, encode, score_run
    record = decode(record_json)
    content_id = digest(record)
    normalized_json = encode(record)

Campaign callers construct a `CampaignSpec` and call `compile_campaign`. The
result contains stable trial identities and requested execution profiles only;
it does not create jobs, attempts, workspaces, or runtime observations.

The offline CLI never discovers inputs from the current working directory and
does not install software, create workspaces, access credentials, submit to a
provider or model, retry an execution, operate a connector, invoke native
execution, publish remotely, promote, or delete. `capabilities` reports those
boundaries directly. `conformance` validates packaged schemas and the portable
import boundary without requiring a repository checkout.

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

The publication layer accepts explicit `AdmittedEvidence` and
`ExecutionResultImport` records through `ObjectStore`. `seal_result_set` binds
every campaign trial and verifies all referenced bytes before persisting a
canonical revision. `publish_manifest` creates immutable local public summaries;
it does not upload data or configure access controls. `blind_result_set` creates
a packet and a separate private mapping. `import_analysis_result` validates an
externally supplied result; `unblind` requires a persisted terminal result and
the original verified mapping. See [the result contract](docs/contracts/results-and-analysis.md).

The package is alpha software. codex.skill-evaluation/v1 compatibility is
covered by transferred fixtures and parity tests, while future portable
protocol layers will receive their own versioned identities.

## Development

    PYTHONPATH=src python -m unittest discover -s tests -v
    uv build

Focused publication/analysis checks are:

    PYTHONPATH=src:tests python -m unittest test_object_store test_publication -v

Focused offline CLI and conformance checks are:

    PYTHONPATH=src:tests python -m unittest test_cli -v

`tests/check-groups.toml` records the module contract for each layer. Full
discovery remains the conservative checkpoint and CI command.

The consumer-owned CI tests Python 3.11, 3.12, and 3.13, builds both distribution
formats, and imports the installed wheel. Public-repository jobs run only on
GitHub-hosted runners. While the repository is private, the same jobs select
the Jones runner label. Jones organization runner policy separately denies
public repositories access to Jones self-hosted runners.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Report
security issues using the process in [SECURITY.md](SECURITY.md). Agent-Evals is
licensed under the [Apache License 2.0](LICENSE).

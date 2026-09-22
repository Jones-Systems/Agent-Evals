# Portable evaluation protocol

## Identity and compatibility

The current normative wire identity is the closed JSON envelope
codex.skill-evaluation/v1. Positive Case.version, Variant.version, and
Rubric.version values are content revisions, not wire versions. Unknown
schemas, kinds, fields, and duplicate keys fail closed.

The Python API is a façade over this versioned data contract. Encoded bytes,
semantic validation, canonical digests, the checked-in JSON Schema, and the
fixture corpus are compatibility surfaces. A future generalized protocol must
use an independent versioned identity and lossless projections; it must reject
records that cannot be represented rather than silently changing v1 bytes.

## Records and bindings

Cases bind public-safe provenance, trigger class, task content, admitted assets,
allowed output paths, and any human-review requirement. Variants bind exact
skill text. Runs bind case, variant, and input digests; arm and mode; intended
runtime; explicit status and reason; and optional immutable evidence.

Rubrics bind the exact case digest and a closed set of deterministic checks.
Scores bind the exact run, rubric, and evidence plus grader provenance,
per-check results, ordered dimension measures, and optional judge and human
assessments. Missing metrics and unmeasured checks remain null; they never
become zero or success.

## Validation and content identity

Text, collections, numeric values, identifiers, relative asset paths, and
record depth are bounded. Public-safe review is explicit. Secret-like content,
control characters, unsafe paths, duplicate or overlapping assets, nonfinite
numbers, and inconsistent digest or status bindings are rejected.

Canonical SHA-256 digests cover deterministic, sorted, compact JSON content.
They establish content equality only. They do not prove observation
authenticity, isolation, authorization, or safety.

## Grading, judging, and comparison

Deterministic checks run before optional judging. A deterministic failure cannot
be overridden by a judge. A judge packet exposes only admitted assets, response,
instructions, and content bindings; it omits arm, case, controller, and runtime
assignment metadata. Judge and human results bind exact predecessor digests.

Rescoring consumes preserved evidence and does not rerun an agent. Comparison
requires exactly one baseline, skill, and do-nothing arm with a common variant,
runtime, mode, case, and rubric. It reports descriptive paired differences for
one matched triplet and always leaves efficacy inconclusive; it is not evidence
of general validity.

## CLI

The foreground CLI emits the schema, validates and normalizes one record, or
grades preserved evidence. Machine records are JSON on stdout. Bounded,
sanitized invalid-input status is written to stderr with exit status 2.

No command creates a workspace, executes an agent, contacts a provider, imports
connector/browser code, installs software, publishes results, promotes a skill,
changes credentials, or claims serving qualification.

## Campaign compilation

The independent `codex.agent-evals-campaign-spec/v1` contract binds exact
suite, input, case, and requested-profile identities; a complete case-by-arm
profile matrix; repetitions; declared profile variance; outcome handling;
compile-time budget bounds; safety-policy references; and a revisioned
counterbalance policy. Profiles describe requests only and contain no observed
runtime fields.

Compilation produces `codex.agent-evals-resolved-campaign/v1`. Every ordered
trial covers one case, arm, and repetition. Its stable ID derives only from the
campaign-spec digest, case, arm, repetition, and evaluation stage. Compiler and
counterbalance revisions are explicit. Fixed order preserves declared arm
order; balanced rotation rotates it deterministically, including when the
repetition count does not complete a rotation cycle.

Budget limits are allocation bounds checked during compilation. They do not
represent mutable counters or observed spend. The campaign contracts and
compiler do not create workspaces, jobs, queue records, operational attempts,
runtime observations, retries, analyses, publications, or significance claims.

## Backend boundary

Workspace, native execution, provider, and connector/browser backends are
future excluded work. If introduced, each must have an independent versioned
interface, opaque identities, capability declaration, lifecycle reconciliation,
known-no-effect versus unknown-effect semantics, bounded capture, and direct
qualification evidence. The portable core must remain independent of backend
implementations.

Authority effect: none.

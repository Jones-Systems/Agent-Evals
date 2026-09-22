# Immutable results and blinded analysis

The local publication layer imports completed or incomplete observations; it
does not execute trials, models, judges, conversations, or runtime controllers.
Every wire envelope names one closed versioned record schema. Packaged JSON
Schemas describe structural constraints; Python validation additionally checks
campaign bindings, reference integrity, ordering, and predecessor relations.

## Storage and access labels

`ContentRef` contains only a SHA-256 digest, byte size, media type, record schema,
and access class. It never includes a filesystem location. The caller selects a
trusted local object-store root independently of these portable references.
Validation occurs before admission. Objects are immutable and content addressed;
repeating identical bytes is idempotent and mismatching existing bytes fail.
Same-directory staging and no-overwrite finalization prevent concurrent writers
from replacing an admitted object. Files and the parent directory are fsynced.
Ordinary failures and cancellation unwind the exact writer-owned staging file.
Uncatchable process/host termination may leave a staging file; this library does
not sweep it or delete retained objects. A failure after finalization can leave
the complete immutable object present; callers reconcile by reading its digest.

Access classes are data labels, not operating-system permissions or publication
authorization. The root is trusted and must not be modified by an adversarial
concurrent actor. A content digest proves equality, not authenticity or safety.
There are no retention enforcement or deletion APIs.

## Sealing and publication

`ExecutionResultImport` binds campaign digest, trial identity, requested profile
digest, nullable observed profile digest, completion, and nullable evidence ref.
Missing observation stays null. No backend, job, attempt, provider-session, or
workspace IDs are imported. `AdmittedEvidence` contains response and output text
explicitly selected by the caller; importing it is a trust boundary, not an
automatic redaction or authenticity decision.

`seal_result_set` requires exactly one entry per resolved trial, rejects dangling
or mismatched references, and sorts entries by trial identity. Execution
completion, evidence verification, and public projection are different states.
An entry classified usable needs complete execution, matching requested and
observed profile digests, and verified evidence. Other classifications preserve
missing and incomplete observations without promoting them. A new revision binds
its immutable predecessor and increments its revision; old bytes never change.

`publish_manifest` creates a closed allowlist projection containing only public
summary refs, the source digest, fixed redaction revision, public access class,
and retention-policy digest. Each public summary contains classification and
missingness enums. No public string slot accepts arbitrary text or identity:
digest slots accept exactly 64 lowercase hexadecimal characters, and all other
text slots are enums. Raw evidence, prompts, traces, private locations, workspace,
credentials, account, conversation, browser, and session identifiers cannot be
represented as fields. This is structural exclusion, not secret-regex filtering.
A retention-policy digest refers to caller-owned policy; this layer neither
interprets it nor authorizes a destination. Manifest creation is local only.

## Blinding and immutable derivation

`AnalysisSpec` contains a rubric, instructions, and numeric bounds. The caller
must admit this text as suitable for the analysis. Packets sort by admitted
evidence content and assign sequential neutral labels. Equal admitted content
produces equal packet bytes independently of arm metadata; ties use trial order
only for the private mapping. Missing evidence is explicit and scored null.
Packets contain only the spec and neutral evidence items, with no trial, arm,
variant, requested/observed runtime, workspace, or controller metadata. Response
content may still reveal semantic clues; neutralization does not guarantee
psychological or semantic blindness.

The private `NeutralLabelMapping` binds the result-set and packet references to
trial/arm identities. Mapping verification reconstructs the original projection
without writing objects. Results bind packet and mapping digests, requested and
nullable observed analysis-profile digests, status, scores, and an optional
immutable predecessor. Requested and observed analysis profiles remain separate;
a terminal result need not imply they matched or that the result is reliable.
Scores must fit the original bounds, preserve every neutral label, and remain
null when evidence is absent. Rescoring imports a new `AnalysisResult` revision
referencing its predecessor; it never executes a judge.

`unblind` reads and digest-validates a persisted result, rejects pending results
and altered mappings, then persists an `UnblindedComparison` referencing that
result and mapping. Comparisons expose descriptive per-trial scores only. They
do not claim statistical significance, causal attribution, acceptance, or live
execution success. Mapping and comparison records stay private.

## Verification contract

The `publication-analysis` group in `tests/check-groups.toml` contains
`test_object_store` and `test_publication`. It covers atomic/no-overwrite storage,
failure cleanup, metadata/reference integrity, complete trial coverage, public
schema exclusion, state separation, deterministic neutral packets, mapping
integrity, missingness, requested/observed separation, and immutable derivations.
The full discovery command additionally runs portable-core and campaign tests.
Tests use bounded fixture data with creator-owned temporary directory cleanup;
no external services, credentials, models, or workspaces are involved.

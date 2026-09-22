# Roadmap

The current release is the portable deterministic core only: closed records,
validation, content hashing, schema emission, fixture parsing, grading,
rescoring, blinded judge packets, and comparison.

Future, excluded layers may define independently versioned contracts and
implementations for:

- suites and immutable campaign compilation;
- workspace materialization, isolation, capture, and settlement backends;
- native/local agent execution backends;
- provider-specific and connector/browser execution backends;
- result storage, publication, and serving qualification.

Those capabilities are not present or implied by this repository today.
Adding one requires its own threat model, lifecycle and unknown-effect rules,
conformance evidence, and explicit authority. The portable core will not import
backend implementations.

Authority effect: none.

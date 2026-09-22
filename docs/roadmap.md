# Roadmap

The current stack contains the portable deterministic core plus closed,
workspace-independent campaign specification and deterministic compilation,
immutable local result storage, closed public-summary manifests, and blinded
analysis packets with imported results and immutable comparisons.
Campaign compilation resolves requested profiles, bounded allocations, stable
trial identities, and fixed or balanced ordering; it performs no execution.

Future, excluded layers may define independently versioned contracts and
implementations for:

- workspace materialization, isolation, capture, and settlement backends;
- native/local agent execution backends;
- provider-specific and connector/browser execution backends;
- remote publication destinations, access-control enforcement, and retention deletion;
- model/judge execution and serving qualification.

Those capabilities are not present or implied by this repository today.
Adding one requires its own threat model, lifecycle and unknown-effect rules,
conformance evidence, and explicit authority. The portable core will not import
backend implementations.

Authority effect: none.

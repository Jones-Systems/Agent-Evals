# Agent guidance

## CI checks

The `python / Linux / Python 3.13` check is required before merge. It compiles
source and tests and runs the complete unittest suite on pull requests and
pushes to `main`, as well as nightly and manual runs.

The `python / Package` check is advisory and runs nightly or through
`workflow_dispatch`. It retains the existing package build and installed-wheel
smoke test; successful qualification on `main` retains artifacts for 14 days.
Use a manual run on the task branch when a change needs package qualification
before merge.

All other checks are advisory. Fix an advisory failure or track it in an issue;
it does not block merge. This declaration does not configure GitHub rulesets.

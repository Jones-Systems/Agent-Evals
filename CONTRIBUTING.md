# Contributing

Agent-Evals welcomes focused issues and pull requests that preserve its
workspace-independent, offline-first boundaries.

Before opening a pull request:

1. Keep changes narrowly scoped and document user-visible contract changes.
2. Add or update tests for changed behavior.
3. Run `PYTHONPATH=src python -m unittest discover -s tests -v`.
4. Run `uv build` when package metadata or packaged files change.

Pull requests should explain the problem, the chosen approach, and the checks
run. Please do not include credentials, private evaluation data, proprietary
prompts, or other sensitive material in issues, commits, fixtures, or logs.

By contributing, you agree that your contribution is licensed under the
Apache License 2.0 that covers this repository.

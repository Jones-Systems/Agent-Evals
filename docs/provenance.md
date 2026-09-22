# Provenance and migration

The first portable-core layer was adapted from public-safe Codex-V3 sources at
commit b30fad5a6b7b52fa00694c3d24d86579af044794:

- src/codex_v3/skill_evaluation.py
- src/codex_v3/resources/skill-evaluation-v1.schema.json
- tests/test_skill_evaluation.py
- tests/fixtures/skill_evaluation/cases.json
- tests/fixtures/skill_evaluation/rubrics.json

The transferred contract retains the codex.skill-evaluation/v1 selector,
record names and fields, semantic validation, canonical JSON hashing, schema,
fixture bytes, grading behavior, judge-packet binding, rescoring, and
comparison semantics.

Workspace creation and capture, AgentInput, adapter invocation, live runtime
execution, and the Codex-V3 fixture pilot were intentionally not transferred.
The portable package accepts completed Run records and grades preserved
evidence. This separation prevents the core package from implying sandbox,
provider, connector, or serving qualification.

Python imports move from codex_v3.skill_evaluation to agent_evals. The
agent-evals grade command supersedes the old module's rescore spelling; rescore
remains a CLI alias. The schema is packaged under agent_evals/resources/.

Authority effect: none.

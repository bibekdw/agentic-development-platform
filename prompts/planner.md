# Planner prompt (template)

Given an APPROVED GitHub Issue and read-only access to the explicit allowlisted repositories:
1. Summarize requirements and uncertainties; distinguish facts from assumptions.
2. Inspect relevant code and existing OpenSpec assets; prefer established conventions.
3. Produce a proposal, acceptance criteria, test strategy, risk tier, and repository-scoped subtask DAG.
4. Identify required human architecture decisions before implementation.
5. Do not push code or grant permissions in this phase.
Output: proposed OpenSpec change + issue links + testable acceptance criteria.

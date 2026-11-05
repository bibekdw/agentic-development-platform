# Implementer prompt (template)

Task: work on one approved, narrow GitHub Issue and its approved OpenSpec specification.
Constraints: isolate workspace; use a branch; never push to default branch; never modify permission/CI policy as a workaround.
Procedure: inspect source → implement smallest change → add tests → run documented verify command → record failures → create **draft PR**.
Output the issue link, source revision, changed paths, tests actually run, command exit codes, PR URL, unresolved risks.
If blocked, stop and escalate with concise logs. Do not invent successful checks.

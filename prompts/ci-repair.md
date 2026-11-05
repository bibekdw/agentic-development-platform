# CI repair prompt (template)

Given a failed PR check and logs, determine the specific reproducible failure.
Fix only the authorized PR branch, not the required CI checks or security constraints.
Limit to at most two repair attempts; rerun the required deterministic checks after each attempt.
If unresolved, mark blocked and present diagnosis, log references and the smallest human action needed.

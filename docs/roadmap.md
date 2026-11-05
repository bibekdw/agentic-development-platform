# Execution checklist (public-first)

| Session | Action | Gate |
|---|---|---|
| 1 | Generate five repositories, review files, run offline unit tests | Scaffolds and tests created |
| 2 | Publish GitHub repositories and add branch protection | Remote repo links and branch rules verified |
| 3 | Run Spring, worker, UI builds with dependencies | Three application CI builds green |
| 4 | Run Docker Compose + end-to-end smoke | Browser/API/worker sample works together |
| 5 | Install gh-aw locally and compile read-only issue triage | Compiled workflow reviewed, successful run |
| 6 | Introduce approved issue → isolated coding agent → draft PR | PR with tests, logs and reproducible output |
| 7 | Independent AI PR review and bounded CI repair | Reliable success and failure-path evidence |
| 8 | Cross-repo issue dependencies and SHA-pinned integration manifest | Combined tests against exact PR revisions |
| 9 | Staging-only automation, release gates, interrupted-run recovery | No unapproved production capability |
| 10 | Benchmark representative tasks and publish truthful results | Public metrics / diagrams / demo runbook |

### Day 2 execution prompt

Inspect all five published repositories and their Actions/rulesets. Implement branch protections and required status checks. Do not enable an agent with write permissions until read-only CI passes. Show actual repository URLs, status-check names and executed commands. Do not start the next day until those are verified.

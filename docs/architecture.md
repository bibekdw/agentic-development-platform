# Architecture and boundaries

```text
Human-created/approved GitHub Issue
             |
       OpenSpec planning
             |
   GitHub-native orchestration (gh-aw)
             |
  coding-agent job in isolated workspace
             |
 draft PR per repository + required CI
             |
  independent AI review / bounded repair
             |
 pinned cross-repo change manifest
             |
 disposable integration environment
             |
      human approval for release
```

- GitHub Issues and PRs are the work ledger. No separate coordinator database in the initial design.
- Use **one coding agent and one independent review mechanism** at first, not an uncontrolled agent swarm.
- `gh-aw` source workflows need compilation into reviewed `.lock.yml` files; an example Markdown file alone is not executable.
- Deterministic checks (unit, API, smoke tests, static analysis) decide pass/fail, not an LLM's opinion.
- Use a manifest containing each included repository and full immutable commit SHA; do not rely on mutable `main` tags in integration evaluation.
- Initial sample does not simulate real authorization, database migrations, or production deployment. Add them only when needed to test a specific ADLC capability.

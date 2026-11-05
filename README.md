# Agentic Development Platform (ADLC) — public-first starter

**Status: scaffold / reference implementation, not an autonomous production system.**

A GitHub-native agentic development lifecycle, developed in public against four *independent* sample repositories before adoption in private software. The sample task tracker demonstrates a browser → Java API → Java worker flow. The platform adds an auditable change manifest, policy prompts, sample workflow contracts, and deterministic validation.

## Workspace topology

| Repository | Technology | Role |
|---|---|---|
| `agentic-development-platform` | Python 3 standard library + docs | Generic coordinator contracts, validation, prompts |
| `adlc-sample-backend` | Java 21 / Spring Boot | REST task API, temporary in-memory state |
| `adlc-sample-worker` | Java 21 | Polls API and completes queued tasks |
| `adlc-sample-ui` | Next.js / TypeScript | Task creation and status UI |
| `adlc-sample-deploy` | Docker Compose / GitHub Actions | Composes the other three; integration smoke test |

The sample app intentionally has no production database or authentication. **Never deploy it publicly with real data.** This is a test fixture for ADLC, not a production app.

## Local checks (no AI subscription needed)

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_manifest.py examples/change-manifest.json
```

For the integrated sample, clone the five repositories into one parent directory and run:

```bash
cd ../adlc-sample-deploy
docker compose up -d --build
python3 scripts/smoke_test.py
docker compose down
```

Docker, Maven, Node and dependencies are required for full application builds. These commands are not yet verified in a Docker environment by the scaffold author.

## What is reusable

- `AGENTS.md` / `templates/AGENTS.md`: least-privilege, review-focused agent instructions
- `prompts/`: planning, implementation, review, bounded CI-repair instruction contracts
- `schemas/change-manifest.schema.json`: cross-repository pinned-commit contract
- `scripts/validate_manifest.py`: deterministic validation (stdlib, no network)
- `docs/`: rollout, architecture, threat model, and portfolio evidence plan
- `examples/gh-aw/`: **uninstalled** read-only workflow example — must be compiled/reviewed before use

## Implementation order

1. Publish the five skeleton repositories (manual step via workspace `publish-to-github.sh`).
2. Confirm builds and Docker integration, then fix any startup issues.
3. Enforce branch protections, CI and code ownership decisions.
4. Compile and manually test a read-only `gh-aw` workflow before adding any write capability.
5. Add an issue → coding agent → draft PR path with independent review.
6. Implement cross-repository revision pinning and merged staging checks.
7. Benchmark measured results; only then adapt to private application repositories.

See [implementation roadmap](docs/roadmap.md), [threat model](docs/security.md), and [publishing guide](docs/publishing.md).

## License and attribution

The starter files are MIT-licensed. GitHub Agentic Workflows and GitHubNext Agentics are **external projects**; they are referenced, not vendored. Check upstream licenses and record attribution for any code copied later.

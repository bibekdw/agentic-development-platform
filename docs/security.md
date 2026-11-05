# Threat model and safety gates

## Assets
Source repositories, GitHub Actions credentials, CI workflows, secrets, release approvals, user data, container runner host.

## Credible threats
- Prompt injection through GitHub Issues, PR text, fetched documents, and CI logs.
- Coding agent modifies CI gates, protected paths, branch rules, or agent instructions.
- Privileged workflow executes untrusted PR code with writable token or secrets.
- Agent loops on failing tests, consumes budget, or silently downgrades tests.
- Cross-repo drift, stale SHA selection, and tests run against a different source revision than the proposed release.

## Required controls before enabling writes
1. Human-owned GitHub repo creation; separate least-privilege GitHub App credentials when required.
2. Protected default branches, required status checks, and reviewer rules.
3. Coding agent may work on a branch and open a **draft PR**; no direct default-branch pushes.
4. Path-policy protections for `.github/workflows/**`, agent instructions, security code, and deployment credentials.
5. Isolated execution with read-only production access (prefer no production access at all).
6. Network egress restrictions where practical; pin third-party actions to reviewed commits for production.
7. Limit agent CI-repair attempts to **two**, then escalate with diagnostics.
8. Human approval for security changes, infrastructure privileges, migrations, and all production releases.
9. Store no real secrets in public sample repos or example configuration.

This skeleton intentionally does not provision branch rules or working credentials. Configure and verify those explicitly before enabling an autonomous write path.

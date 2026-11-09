# Epic/story planning and OpenSpec approval

Milestone ends at **Ready for implementation**. No application code is generated here.

## Components

| Piece | Where | Deterministic? |
|---|---|---|
| Manual trigger + serialization | `.github/workflows/adlc-plan-story.md` (compiled to `.lock.yml` with `gh aw compile`) | yes |
| Preflight (allowlist, open issue, requester permission, acceptance criteria) | `scripts/preflight.py` | yes |
| Read-only planner context | `scripts/build_planner_context.py` | yes |
| Planner (writes files only) | gh-aw agent, Copilot engine | **no (AI)** |
| Artifact validation, path validation, draft PR publication, tracking, status | `scripts/publish_plan.py`, `scripts/validate_planning_pr.py`, `scripts/planning.py` | yes |
| Planning-PR check in each participating repo | `templates/planning-pr-validate.yml` (installed per repo) | yes |
| Reconciliation / readiness | `.github/workflows/reconcile.yml`, `scripts/reconcile.py` | yes |

The agent has **no GitHub credentials and no GitHub tools**. It reads files under `adlc-context/` and writes files
under `/tmp/gh-aw/agent/plan/`, then calls one safe-output tool, `submit_plan`. A separate job (not the agent)
validates and publishes. The agent cannot choose the change id, write plan metadata (`adlc-plan.json`), touch other
paths, create or merge PRs, or approve anything.

## Flow

```text
workflow_dispatch(story_repo, story_number, dry_run)
  -> preflight job   (read token; live runs may comment questions on the story -> Needs clarification)
  -> agent job       (context built with read-only token; agent writes plan files)
  -> submit_plan job (threat detection gate; validate; publish draft PRs; tracking issue; story status comment)
maintainer reviews + manually merges each planning PR   (merge == approval of that repo's plan)
reconcile workflow (every 15 minutes, or run by hand) -> refreshes PR states -> records merged SHA + approved revision -> Ready for implementation
```

* Change ID: `<repo>-<issue>-<slug>` (`adlc-sample-backend-12-...`, max 64 chars), computed once from the story
  title and then fixed by the tracking record. Issue numbers are always `owner/repo#n`.
* Branch for every affected repo: `adlc/plan/<change-id>`. Folder: `openspec/changes/<change-id>/`.
* Each change folder gets a machine-written `adlc-plan.json` (story, epic, revision, repository, contract refs,
  dependencies). It is validated by the check, never trusted from the agent.
* `dry_run: true` (default for the planner) generates and validates everything and uploads the proposed PR bodies,
  artifacts and record as the `adlc-publish-diagnostics` artifact. It performs only reads (clones, API reads).

## Coordination record

Tracking issue in this repo, label `adlc-coordination`, title `[ADLC plan] <owner/repo#n> <change-id>`. The body has a
status table and a JSON block (`adlc-coordination:v1`, see `scripts/planning.py`): story, epic, planning revision,
source hash, repositories (PR, state, merged SHA, approved revision, validation, branch, per-file generated hashes,
scope, dependencies), shared contracts, blockers, workflow run links, last failure with recovery action. One status
comment per story carries marker `<!-- adlc-status:<change-id> -->` and is edited in place.

## Readiness (deterministic)

Ready only if: no open questions, no failed validation, no unmet blockers, every repository PR merged with a recorded
SHA, `approved_revision == planning_revision`, contracts consistent (one owner, every reference resolves, owner lists
its contract), validation passed. Closed-unmerged PRs and changed story content (new revision) block readiness until
re-approved. AI review is advisory only and never proves contract compatibility; checks verify explicit references.

## Retry and recovery

* Retries find the tracking issue by `owner/repo#n` (whole-token match, `#12` never matches `#123`) and the PRs by
  branch, and continue only missing work. A re-run with identical plan content produces no commit, no PR, no issue
  and no comment.
* Human edits are preserved: each generated file's hash is stored. A file is regenerated only if it still equals the
  last generated version; if it was edited or deleted by a human, publishing stops for that repository and reports a
  reconciliation conflict (nothing is overwritten). Extra files added by humans are never touched.
* Repositories are processed independently. If one fails, completed PRs remain, the failure and recovery action are
  recorded, the status comment lists the partial failure, and a re-run resumes the missing repository.
* Not automated yet: a new revision of an already merged plan (reported as a failure; open a follow-up PR manually),
  automatic resolution of `blocked_by` dependencies (the field exists and blocks readiness; set it manually).

## Authentication (GitHub App, short-lived tokens)

No permanent planner token exists. Each job mints an installation token at runtime with
`actions/create-github-app-token` (pinned by SHA) from the App credentials, scoped to the platform repo plus the
repositories in `configs/repositories.json` (`scripts/app_scope.py`), with per-step permissions:

| Operation | Job / step | Token permissions |
|---|---|---|
| Read story, comments, specs, code | preflight, agent pre-step (context) | contents, issues, pull requests: **read** |
| Post clarification questions | preflight (live runs only) | issues: write (+ contents/pull requests read) |
| Write planning branches, draft PRs, tracking issue, status comment | `submit_plan` (live runs only) | contents, pull requests, issues: **write** |
| Reconcile | reconcile workflow (scheduled and manual; not in dry runs) | issues: write (+ read) |
| Dry runs | all | read only |

Planning PR validation runs because PRs are opened with the App token (PRs opened with `GITHUB_TOKEN` do not trigger
workflows). The check itself runs with a read-only token and no secrets. The token is passed to `gh`/`git` only through
`GH_TOKEN` and an in-memory git config header (also masked); it is never written to generated artifacts, the clone
config or logs. The App has no administration and no workflows permission, so even a bug cannot change workflow files
or repository settings. Copilot inference uses separate authentication (`copilot-requests: write` with the default
`GITHUB_TOKEN`, as in the audit workflow); the App key is not used for inference.

Bounds: agent step 30 min, `max-turns: 40`, `max-ai-credits: 500`, 3 runs/user/hour, jobs have timeouts, global
concurrency group `adlc-planning-global` (one planning or reconcile run at a time). Diagnostics are uploaded with
`if: always()`.

Scheduled reconcile: `reconcile.yml` runs every 15 minutes on `main` with `--all`. It reconciles every open
coordination issue, skips changes already Ready for implementation or cancelled, rewrites the tracking issue and
status comment only when something changed, and keeps going if one change fails (the run then ends red and names the
failing change). It only reads PR state and writes the tracking issue and the story status comment; it cannot merge or
edit PRs. To reconcile one change immediately, dispatch it with the change id. GitHub disables scheduled workflows
after 60 days without repository activity; re-enable it from the Actions tab if that happens.

## One-time GitHub App setup (you perform; nothing here creates credentials)

1. GitHub > Settings > Developer settings > GitHub Apps > **New GitHub App**. Name e.g. `adlc-planner`. Homepage URL: the
   platform repository URL. **Uncheck** Webhook "Active". Uncheck "Expire user authorization" etc. (not used).
2. Repository permissions - set exactly: **Contents: Read and write**, **Pull requests: Read and write**,
   **Issues: Read and write**, **Metadata: Read-only** (mandatory). Leave everything else, notably *Administration*,
   *Workflows*, *Actions*, *Secrets* and *Environments*, at "No access". (Actions: Read is only needed if you later add
   run inspection; it is not needed now.)
3. "Where can this GitHub App be installed?" **Only on this account**. Create the App.
4. Note the **Client ID** (not a secret). Generate a **private key** (downloads a `.pem`).
5. Install the App on the owner account with **Only select repositories**: `agentic-development-platform`,
   `adlc-sample-backend`, `adlc-sample-worker`, `adlc-sample-ui`, `adlc-sample-deploy`.
6. In `agentic-development-platform` > Settings > Secrets and variables > Actions: add **variable**
   `ADLC_APP_CLIENT_ID` and **secret** `ADLC_APP_PRIVATE_KEY` (paste the whole PEM). Optionally scope the secret to an
   environment with required reviewers (then add `environment:` to the workflow in a reviewed change). Delete the local `.pem`.
7. Rotate by generating a new key, updating the secret, deleting the old key in the App settings.

Values you must supply: App Client ID, App private key, installation on the five repos, and (after the platform
setup PR merges) the 40-character platform merge SHA to put into each repo's `ADLC_PLATFORM_REF`.

## OpenSpec pin and validation

`configs/openspec.json` is the only place the version appears (package, version, node minimum, validate arguments).
The planning-PR check (in the platform scripts, executed from a pinned platform commit) and the publisher both read
it, so installed repositories carry no version string. The pinned package and version are in that file (tested with
the CLI, Node >= 20.19 required; workflows use Node 22). Change it only in a separate setup PR after re-running `tests/test_openspec_cli.py`.

Validation command (verified; non-interactive, scoped to one change, non-zero on failure):

```text
npx --yes @fission-ai/openspec@<pinned> validate <change-id> --type change --strict --no-interactive
```

Notes from testing: a missing change folder yields a misleading "no deltas" message, so the wrapper checks the folder
first; an invalid sibling change does not affect validation of the named change; the extra `adlc-plan.json` is
ignored by OpenSpec; the CLI does not require design/tasks content, so those are checked by `scripts/planning.py`.

## Local checks and what they do not prove

See the final report of the implementing change. Unit tests use mocked GitHub API calls but **real git** against local
bare repositories and the **real pinned OpenSpec CLI**. Behavior that only a GitHub runtime can show (App token
minting, artifact layout between jobs, agent tool availability, threat detection, PR checks being triggered,
`gh` calls against live repos) is listed in the first-live-run checklist.

## Implementation phase (after "Ready for implementation")

Manual workflow `adlc-implement` (inputs: story repo, story number, target repo, dry run - default true), one repository per run.

1. **Gate** (deterministic): record is Ready, the repo's plan PR is merged and approved for the current revision, dependencies are implemented, the repo has an entry under `implementation.repositories` in `configs/repositories.json`, the requester has write access, and no implementation PR is already open.
2. **Agent** edits only a working copy (no credentials). It may run the repo's build in its sandbox; that result is a hint only.
3. **Validate**: the diff against the pristine base commit may touch only `allowed_path_prefixes` (default `src/`) and tick checkboxes in the change's `tasks.md`. Build files, `.github/`, `AGENTS.md`, `openspec/specs/`, symlinks, binaries and oversized diffs are rejected.
4. **Isolated verify**: the validated tree is built in a container with no secrets (`implementation.repositories.<repo>.verify`). Only then is the write token minted.
5. **Publish**: a DRAFT PR on `adlc/impl/<change-id>`; a failed verification opens no PR and is recorded in the status comment.
6. The repo's own CI runs; a human reviews and merges. `reconcile` then records "Implementation in review" -> "Implemented" (all affected repos merged).

Retry after a failure is a manual re-dispatch. Merging the implementation PR is the acceptance of the code; nothing is merged or deployed automatically.

### Fail-before check (in the isolated verify step)

The new or changed test files (`test_path_prefixes`, default `src/test/`) are also run against the ORIGINAL source. They are expected to fail there. The PR body and the record show the result: `fails_without_change` (good), `passes_without_change` (the tests may not test the change), `no_test_changes`. It is a visible signal, not a gate.

## Advisory AI review (`adlc-review`)

Manual workflow (inputs: story repo, story number, target repo, PR number, dry run - default true). The reviewer reads the approved spec, the PR diff, a read-only copy of the repo at the PR head and the platform's deterministic evidence (verify exit code, fail-before result). It has no network, no build tools and no credentials, and writes one structured `review.json`.

- Rubric (from OpenSpec's verify skill): completeness (ticked tasks vs evidence), correctness (every spec scenario vs a test that would fail without the change, requirements vs code), coherence (scope of every changed file, design and repo rules), plus risks and assumptions.
- Deterministic code validates the schema, derives the recommendation from the findings (the agent cannot set it), reconciles the scope table with the real diff, neutralises markup (no HTML, links, mentions), and posts ONE comment per change, edited in place.
- The comment is advisory: no approval, no request-changes, no blocking, no merge. The comment-only token cannot write repository contents.

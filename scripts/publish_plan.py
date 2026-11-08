#!/usr/bin/env python3
"""Validate an agent-produced plan and publish it as draft planning PRs.

Pipeline: story -> (preflight ran earlier) -> planner output -> artifact validation ->
allowed-path validation -> draft PR publication -> coordination record / status update.

The planner (AI) only writes files. Everything here is deterministic: the agent cannot
choose the change id, write metadata, touch other paths, approve or merge anything.
Retries reuse the tracking issue, branches and PRs and never overwrite human edits.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planning as p  # noqa: E402
import validate_planning_pr as vp  # noqa: E402
from ghclient import GitHubClient, GitHubError, git, observe_prs  # noqa: E402

MAX_FILES = 40
MAX_FILE_BYTES = 200_000
CONTRACT_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,62}\Z")
PLAN_KEYS = {"story", "epic", "summary", "open_questions", "affected_repositories", "shared_contracts"}


class PlanRejected(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class RepoFailure(Exception):
    def __init__(self, message, recovery, validation=None):
        super().__init__(message)
        self.recovery = recovery
        self.validation = validation


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tracking_title(rec):
    return f"[ADLC plan] {rec['story']} {rec['change_id']}"


# --------------------------------------------------------------------------- plan loading
def collect_files(root, change_id):
    """Read one repository's generated tree. Rejects symlinks, odd paths, binary or huge files."""
    root = Path(root)
    files, errors = {}, []
    if not root.is_dir():
        return {}, [f"no generated files for {root.name}"]
    for f in sorted(root.rglob("*")):
        if f.is_symlink():
            errors.append(f"symlink not allowed: {f.relative_to(root).as_posix()}")
            continue
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        errors += p.check_changed_paths(change_id, [rel])
        data = f.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            errors.append(f"{rel}: larger than {MAX_FILE_BYTES} bytes")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{rel}: not UTF-8 text")
        if b"\x00" in data:
            errors.append(f"{rel}: binary content")
        files[rel] = data
    if len(files) > MAX_FILES:
        errors.append(f"{root.name}: more than {MAX_FILES} files")
    files.pop(f"openspec/changes/{change_id}/{p.PLAN_META}", None)  # machine-written only
    return files, errors


def load_plan(plan_dir, cfg, story_ref, change_id):
    """Return (plan, {short_name: files}). Raises PlanRejected with every problem found."""
    plan_dir = Path(plan_dir)
    path = plan_dir / "plan.json"
    if not path.is_file():
        raise PlanRejected(["planner produced no plan.json"])
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise PlanRejected(["plan.json is not valid JSON"])
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
        raise PlanRejected([f"plan.json must have exactly the keys {sorted(PLAN_KEYS)}"])
    e = []
    if plan["story"] != story_ref:
        e.append("plan.json story does not match the requested story")
    if plan["epic"] is not None and not p._ref_ok(plan["epic"]):
        e.append("epic must be null or a fully qualified reference")
    if not isinstance(plan["summary"], str) or not plan["summary"].strip():
        e.append("summary must be a non-empty string")
    qs = plan["open_questions"]
    if not isinstance(qs, list) or not all(isinstance(q, str) and q.strip() for q in qs):
        e.append("open_questions must be a list of non-empty strings")
        qs = []
    allowed = set(cfg["allowed_repositories"])
    affected = plan["affected_repositories"]
    names = []
    if not isinstance(affected, list) or (not affected and not qs):
        e.append("affected_repositories must be non-empty unless open_questions are given")
        affected = []
    for i, a in enumerate(affected):
        if not isinstance(a, dict) or set(a) != {"name", "scope", "contract_refs", "depends_on"}:
            e.append(f"affected_repositories[{i}] needs name, scope, contract_refs, depends_on")
            continue
        if a["name"] not in allowed:
            e.append(f"affected_repositories[{i}] {a['name']!r} is not allowlisted")
        if a["name"] in names:
            e.append(f"affected_repositories[{i}] duplicated")
        names.append(a["name"])
        if not isinstance(a["scope"], str) or not a["scope"].strip():
            e.append(f"{a['name']}: scope must be non-empty")
        for key in ("contract_refs", "depends_on"):
            if not isinstance(a[key], list) or not all(isinstance(x, str) for x in a[key]):
                e.append(f"{a['name']}: {key} must be a list of strings")
    for a in affected:
        if isinstance(a, dict) and isinstance(a.get("depends_on"), list):
            for d in a["depends_on"]:
                if d not in names or d == a.get("name"):
                    e.append(f"{a.get('name')}: depends_on {d!r} is not another affected repository")
    contracts = plan["shared_contracts"]
    if not isinstance(contracts, list):
        e.append("shared_contracts must be a list")
        contracts = []
    for i, c in enumerate(contracts):
        if not isinstance(c, dict) or set(c) != {"id", "owner", "location"}:
            e.append(f"shared_contracts[{i}] needs id, owner, location")
            continue
        if not CONTRACT_ID.fullmatch(str(c["id"])):
            e.append(f"shared_contracts[{i}] invalid id")
        if c["owner"] not in names:
            e.append(f"shared_contracts[{i}] owner must be an affected repository")
        if not isinstance(c["location"], str) or p.check_changed_paths(change_id, [c["location"]]):
            e.append(f"shared_contracts[{i}] location must be inside openspec/changes/{change_id}/")
    extra = {d.name for d in plan_dir.iterdir() if d.is_dir()} - set(names)
    if extra:
        e.append(f"generated files for repositories not in the plan: {sorted(extra)}")
    files = {}
    if not qs:
        for n in names:
            files[n], errs = collect_files(plan_dir / n, change_id)
            e += [f"{n}: {x}" for x in errs]
    for c in contracts:
        if isinstance(c, dict) and c.get("owner") in files and c.get("location") not in files[c["owner"]]:
            e.append(f"contract {c.get('id')} location {c.get('location')} is not a generated file of {c['owner']}")
    if e:
        raise PlanRejected(e)
    return plan, files


def merge_plan(rec, plan, cfg, fallback_repo):
    """Merge validated plan into the coordination record (pure)."""
    rec = copy.deepcopy(rec)
    owner = cfg["owner"]
    rec["epic"] = plan["epic"]
    rec["open_questions"] = list(plan["open_questions"])
    wanted = {f"{owner}/{a['name']}": a for a in plan["affected_repositories"]}
    existing = {r["name"]: r for r in rec["repositories"]}
    for name, r in existing.items():
        if name not in wanted and wanted and r["pr_state"] != "none":
            raise PlanRejected([f"{name} has a planning PR but the new plan drops it; reconcile manually"])
    repos = []
    for name, a in wanted.items():
        entry = existing.get(name) or {"name": name, "plan_pr": None, "pr_state": "none", "plan_revision":
                                       rec["planning_revision"], "validation": "pending", "contract_refs": []}
        entry["contract_refs"] = sorted(a["contract_refs"])
        entry["scope"] = a["scope"]
        entry["depends_on"] = sorted(f"{owner}/{d}" for d in a["depends_on"])
        repos.append(entry)
    if not repos:
        repos = [r for r in existing.values()] or [{"name": fallback_repo, "plan_pr": None, "pr_state": "none",
                                                     "plan_revision": rec["planning_revision"],
                                                     "validation": "pending", "contract_refs": []}]
    rec["repositories"] = repos
    rec["shared_contracts"] = [{"id": c["id"], "owner": f"{owner}/{c['owner']}", "location": c["location"]}
                               for c in plan["shared_contracts"]]
    return rec


# --------------------------------------------------------------------------- rendering
def pr_url(repo, number):
    return f"https://github.com/{repo}/pull/{number}"


def pr_title(rec, entry):
    return f"[ADLC plan] {rec['change_id']} ({entry['name'].split('/')[1]})"


def pr_body(rec, entry, run_url, validation_note, summary=""):
    owner, srepo, snum = p.parse_story_ref(rec["story"])
    lines = [
        f"## Planning PR for `{rec['change_id']}`", "",
        "**Merging this PR approves this repository's plan, not the implementation.** "
        "No application code changes are included.", "",
        f"- Story: https://github.com/{owner}/{srepo}/issues/{snum} (`{rec['story']}`)",
        f"- Epic: {rec['epic'] if rec.get('epic') else 'none'}",
        f"- Change ID: `{rec['change_id']}`",
        f"- Planning revision: {rec['planning_revision']}",
        f"- Source snapshot: `{rec['source_hash'][:12]}`", "",
        "### Scope of this repository's work", entry.get("scope", "(see proposal.md)"), "",
        "### Linked planning PRs for the same story"]
    sibs = [r for r in rec["repositories"] if r["name"] != entry["name"]]
    if not sibs:
        lines.append("- none (single-repository story)")
    for r in sibs:
        link = pr_url(r["name"], r["plan_pr"]) if r.get("plan_pr") else "pending"
        lines.append(f"- {r['name']}: {link}")
    lines += ["", "### Shared contracts"]
    contracts = [c for c in rec["shared_contracts"] if c["id"] in entry["contract_refs"]]
    if not contracts:
        lines.append("- none")
    for c in contracts:
        role = "authoritative owner" if c["owner"] == entry["name"] else f"defined by {c['owner']}"
        lines.append(f"- `{c['id']}` ({role}): `{c['location']}`")
    lines += ["", "### Validation", validation_note, "", "### Unresolved questions"]
    lines += [f"- {q}" for q in rec["open_questions"]] or ["- none"]
    if run_url:
        lines += ["", f"Workflow run: {run_url}"]
    return "\n".join(lines)


def tracking_body(rec, status, reasons):
    lines = [f"Status: **{status}**", "", "| Repository | PR | State | Merged SHA | Approved rev |", "|---|---|---|---|---|"]
    for r in rec["repositories"]:
        pr = f"#{r['plan_pr']}" if r.get("plan_pr") else "-"
        sha_ = (r.get("merged_sha") or "-")[:12]
        lines.append(f"| {r['name']} | {pr} | {r['pr_state']} | {sha_} | {r.get('approved_revision', '-')} |")
    if reasons:
        lines += ["", "Outstanding:"] + [f"- {x}" for x in reasons]
    impl_rows = (rec.get("implementation") or {})
    if impl_rows:
        lines += ["", "| Repository | Implementation PR | State | Merged SHA | Verify exit |", "|---|---|---|---|---|"]
        for name, im in impl_rows.items():
            lines.append(f"| {name} | #{im.get('pr')} | {im.get('pr_state')} | {(im.get('merged_sha') or '-')[:12]} | "
                         f"{(im.get('verify') or {}).get('exit_code', '-')} |")
    return "\n".join(lines) + "\n\n" + p.render_record_block(rec)


# --------------------------------------------------------------------------- per-repo publish
def apply_files(worktree, files, generated):
    """Write generated files without clobbering human edits. Returns (hashes, conflicts)."""
    final, conflicts = {}, []
    for rel, data in files.items():
        dest = Path(worktree) / rel
        new = sha(data)
        if dest.exists():
            cur = sha(dest.read_bytes())
            if cur == new:
                final[rel] = new
                continue
            if generated.get(rel) != cur:
                conflicts.append(rel)  # edited by a human since last generation
                continue
        elif rel in generated:
            conflicts.append(rel)  # deleted by a human
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        final[rel] = new
    return final, conflicts


def publish_repo(client, rec, entry, files, workdir, dry_run, run_cli, run_url, out_dir=None):
    repo, cid = entry["name"], rec["change_id"]
    branch = p.planning_branch(cid)
    short = repo.split("/")[1]
    pr = client.find_pr(repo, branch)
    if pr and pr["state"] == "MERGED":
        if entry.get("approved_revision") == rec["planning_revision"]:
            return {"action": "skip-approved"}
        raise RepoFailure(f"plan already merged for an older revision ({entry.get('approved_revision')})",
                          "a new revision of an already merged plan needs a follow-up planning PR; "
                          "this is not automated yet - open one manually or cancel")
    if pr and pr["state"] == "CLOSED":
        raise RepoFailure(f"planning PR #{pr['number']} was closed without merging",
                          "reopen the PR (or delete the branch) and re-run planning")
    work = Path(workdir) / short
    client.clone(repo, work)
    default = client.default_branch(repo)
    base_ref = f"origin/{default}"
    remote = git(work, "rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False)
    git(work, "checkout", "-q", "-B", branch, f"origin/{branch}" if remote else base_ref)

    files = dict(files)
    meta = p.plan_metadata(rec, entry, entry.get("depends_on", []))
    files[f"openspec/changes/{cid}/{p.PLAN_META}"] = (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()
    final, conflicts = apply_files(work, files, entry.get("generated_hashes", {}))
    if conflicts:
        raise RepoFailure("human edits conflict with regenerated artifacts: " + ", ".join(conflicts),
                          "resolve on the planning branch (keep your edits or delete the listed files to accept "
                          "regeneration), then re-run planning; nothing was overwritten")
    git(work, "add", "-A", f"openspec/changes/{cid}")
    if git(work, "status", "--porcelain"):
        git(work, "commit", "-q", "-m", f"ADLC plan {cid} revision {rec['planning_revision']}")
    errors, _ = vp.validate_repo(work, cid, base_ref, repo, run_cli)
    if errors:
        raise RepoFailure("artifact validation failed", "fix the plan (or story) and re-run planning",
                          validation="\n".join(errors))
    note = ("All deterministic checks passed: allowed paths, plan metadata, OpenSpec structure and "
            + ("`openspec validate --strict`." if run_cli else "(OpenSpec CLI skipped)."))
    result = {"action": "update-existing" if pr else "create", "validation": note, "branch": branch,
              "hashes": final, "base": default, "files": files}
    if dry_run:
        return result
    ahead = git(work, "rev-list", "--count", f"origin/{branch}..HEAD", check=False) if remote else "1"
    if ahead not in ("0", ""):
        git(work, "push", "origin", f"HEAD:refs/heads/{branch}", env=client.git_env())
    if pr:
        result["pr"] = {"number": pr["number"], "url": pr["url"]}
    else:
        entry_preview = dict(entry, plan_pr=None)
        body = pr_body(rec, entry_preview, run_url, note)
        result["pr"] = client.create_pr(repo, branch, default, pr_title(rec, entry), body)
    return result


# --------------------------------------------------------------------------- orchestration
def publish(client, cfg, platform, story_ref, plan_dir, run_url, dry_run, out_dir, workdir, run_cli=True):
    owner, srepo, snum = p.parse_story_ref(story_ref)
    story_repo = f"{owner}/{srepo}"
    allowed = {f"{cfg['owner']}/{n}" for n in cfg["allowed_repositories"]}
    if story_repo not in allowed:
        raise PlanRejected([f"{story_repo} is not allowlisted"])
    issue = client.get_issue(story_repo, snum)
    if not issue or issue.get("state") != "open" or "pull_request" in issue:
        raise PlanRejected(["story issue does not exist, is not open, or is a pull request"])
    h = p.source_hash(issue["title"], issue.get("body") or "")

    tracking = client.find_tracking_issue(platform, story_ref=story_ref)
    if tracking:
        rec = p.parse_record_block(tracking["body"])
        if rec["story"] != story_ref:
            raise PlanRejected(["coordination record belongs to a different story"])
        rec = p.bump_revision(rec, h)
    else:
        rec = {"schema_version": 1, "change_id": p.make_change_id(srepo, snum, issue["title"]), "story": story_ref,
               "epic": None, "planning_revision": 1, "source_hash": h, "open_questions": [], "blocked_by": [],
               "runs": [], "cancelled": False, "shared_contracts": [], "repositories": [], "last_failure": None}
    cid = rec["change_id"]
    plan, files = load_plan(plan_dir, cfg, story_ref, cid)
    rec = merge_plan(rec, plan, cfg, story_repo)
    rec = p.apply_pr_states(rec, observe_prs(client, rec))
    if run_url and run_url not in rec["runs"]:
        rec["runs"].append(run_url)

    def persist(status_rec):
        if dry_run:
            return
        status, reasons = p.compute_status(status_rec, allowed)
        body = tracking_body(status_rec, status, reasons)
        nonlocal tracking
        if tracking:
            client.update_tracking_issue(platform, tracking["number"], body)
        else:
            number = client.create_tracking_issue(platform, tracking_title(status_rec), body)
            tracking = {"number": number, "title": tracking_title(status_rec), "body": body}

    persist(rec)  # create/refresh the durable record before touching other repositories
    failures, results = [], {}
    if not rec["open_questions"]:
        for entry in rec["repositories"]:
            short = entry["name"].split("/")[1]
            try:
                res = publish_repo(client, rec, entry, files.get(short, {}), workdir, dry_run, run_cli, run_url)
                results[entry["name"]] = res
                if res["action"] != "skip-approved":
                    entry["validation"] = "passed"
                    entry["plan_revision"] = rec["planning_revision"]
                    entry["branch"] = res["branch"]
                    entry["generated_hashes"] = res["hashes"]
                    if res.get("pr"):
                        entry["plan_pr"] = res["pr"]["number"]
                        entry["pr_state"] = "open"
            except (RepoFailure, GitHubError) as exc:
                if isinstance(exc, RepoFailure) and exc.validation:
                    entry["validation"] = "failed"
                failures.append({"repo": entry["name"], "message": str(exc),
                                 "recovery": getattr(exc, "recovery", "re-run planning; completed work is reused"),
                                 "validation": getattr(exc, "validation", None)})
            persist(dict(rec, last_failure=failures[-1] if failures else rec.get("last_failure")))
    rec["last_failure"] = ({"repo": failures[0]["repo"], "message": failures[0]["message"],
                            "recovery": failures[0]["recovery"], "run": run_url} if failures else None)
    if not dry_run:  # second pass: cross-link sibling PRs now that all numbers exist
        for entry in rec["repositories"]:
            if entry.get("plan_pr") and entry["pr_state"] == "open":
                note = results.get(entry["name"], {}).get("validation", "See the planning PR checks.")
                client.update_pr_body(entry["name"], entry["plan_pr"], pr_body(rec, entry, run_url, note))
    persist(rec)
    status, reasons = p.compute_status(rec, allowed)
    if rec["open_questions"]:
        status, reasons = p.STATUS_CLARIFY, rec["open_questions"]
    comment = p.render_status_comment(rec, status, reasons) if status != p.STATUS_FAILED else \
        f"**ADLC planning status: {status}**\n\n" + "\n".join(f"- {x}" for x in reasons)
    if failures:
        comment += "\n\nPartial failures:\n" + "\n".join(
            f"- {f['repo']}: {f['message']} (recovery: {f['recovery']})" for f in failures)
    if not dry_run:
        client.upsert_status_comment(story_repo, snum, f"<!-- adlc-status:{cid} -->", comment)
    if out_dir:
        write_dry_run(out_dir, rec, comment, results, failures, plan, run_url)
    return {"change_id": cid, "status": status, "reasons": reasons, "failures": failures,
            "actions": {k: v["action"] for k, v in results.items()}, "record": rec}


def write_dry_run(out_dir, rec, comment, results, failures, plan, run_url):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "record.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
    (out / "status-comment.md").write_text(comment)
    (out / "plan-summary.md").write_text(str(plan.get("summary", "")))
    (out / "failures.json").write_text(json.dumps(failures, indent=2))
    for repo, res in results.items():
        d = out / repo.split("/")[1]
        for rel, data in res.get("files", {}).items():
            f = d / "files" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        entry = next(r for r in rec["repositories"] if r["name"] == repo)
        (d / "pr.md").write_text(f"# {pr_title(rec, entry)}\n\n" + pr_body(rec, entry, run_url, res.get("validation", "")))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--story", required=True, help="owner/repo#number")
    ap.add_argument("--plan-dir", type=Path, required=True)
    ap.add_argument("--platform", required=True, help="owner/repo of the platform (coordination) repo")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--dry-run", action="store_true", help="no pushes, PRs, issues or comments")
    ap.add_argument("--out", type=Path, help="write proposed record, PR bodies and artifacts here")
    ap.add_argument("--no-openspec", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads((Path(__file__).resolve().parents[1] / "configs" / "repositories.json").read_text())
    try:
        with tempfile.TemporaryDirectory(prefix="adlc-publish-") as work:
            res = publish(GitHubClient(), cfg, a.platform, a.story, a.plan_dir, a.run_url, a.dry_run, a.out, work,
                          not a.no_openspec)
    except PlanRejected as exc:
        print("PLAN REJECTED:\n" + "\n".join(f"- {x}" for x in exc.errors))
        return 2
    summary = {k: v for k, v in res.items() if k != "record"}
    print(json.dumps(summary, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"### ADLC planning: {res['status']}\n\n```json\n{json.dumps(summary, indent=2)}\n```\n")
    return 1 if res["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())

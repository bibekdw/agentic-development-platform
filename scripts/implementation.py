#!/usr/bin/env python3
"""Deterministic logic for the implementation phase (after a plan is Ready for implementation).

The implementer (AI) only edits a working copy of one repository. Everything here is deterministic:
the gate that decides whether implementation may start, the strict diff rules the agent's result must
satisfy, and the record updates. The agent cannot choose paths outside the profile, touch build, CI,
policy or main-spec files, or mark tasks other than by ticking checkboxes.
"""
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planning as p  # noqa: E402

IGNORED_DIRS = {".git", "target", "node_modules", "__pycache__", ".m2", ".gradle", "build", "dist"}
MAX_TREE_FILES = 5000
BASE_FILE = ".adlc-base-sha"
MAX_TREE_BYTES = 50_000_000
CHECKBOX = re.compile(r"^(\s*- \[)[ xX](\] )", re.M)


class ImplRejected(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def implementation_branch(change_id):
    return f"adlc/impl/{change_id}"


def profile(cfg, short):
    """Merged implementation profile for one repository, or None when the repository has not opted in."""
    impl = cfg.get("implementation") or {}
    repo = (impl.get("repositories") or {}).get(short)
    if repo is None:
        return None
    merged = copy.deepcopy(impl.get("default") or {})
    merged.update(repo)
    return merged


# --------------------------------------------------------------------------- trees and diffs
def snapshot_tree(root):
    """rel-path -> bytes for a repository copy. Ignores build output; rejects symlinks and huge trees."""
    root = Path(root)
    files, errors, total = {}, [], 0
    if not root.is_dir():
        return {}, [f"{root} is not a directory"]
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts) or rel.as_posix() == BASE_FILE:
            continue
        if f.is_symlink():
            errors.append(f"symlink not allowed: {rel.as_posix()}")
            continue
        if not f.is_file():
            continue
        data = f.read_bytes()
        total += len(data)
        files[rel.as_posix()] = data
        if len(files) > MAX_TREE_FILES or total > MAX_TREE_BYTES:
            errors.append("working copy is too large")
            break
    return files, errors


def diff_trees(base, work):
    """(changed: {rel: bytes|None}, errors). None means the file was deleted."""
    b, e1 = snapshot_tree(base)
    w, e2 = snapshot_tree(work)
    changed = {rel: data for rel, data in w.items() if b.get(rel) != data}
    for rel in b:
        if rel not in w:
            changed[rel] = None
    return changed, e1 + e2


def tasks_path(change_id):
    return f"openspec/changes/{change_id}/tasks.md"


def tasks_only_checkboxes(old, new):
    """True when two tasks.md texts differ only by ticked/unticked checkboxes."""
    return CHECKBOX.sub(r"\1 \2", old) == CHECKBOX.sub(r"\1 \2", new)


def path_errors(change_id, rel, prof):
    norm = rel.replace("\\", "/")
    parts = norm.split("/")
    if norm.startswith("/") or ".." in parts or ".git" in parts or not norm:
        return [f"path not permitted: {rel}"]
    if norm == tasks_path(change_id):
        return []
    if any(norm.startswith(prefix) for prefix in prof["allowed_path_prefixes"]):
        return []
    return [f"path not permitted for implementation: {rel}"]


def check_changes(change_id, changed, base_files, prof):
    """Validate a change set against the profile. Returns a list of errors (empty when acceptable)."""
    errors = []
    if not changed:
        return ["the implementer made no changes"]
    if len(changed) > prof["max_files"]:
        errors.append(f"more than {prof['max_files']} changed files")
    total = sum(len(d) for d in changed.values() if d is not None)
    if total > prof["max_total_bytes"]:
        errors.append(f"changed content is larger than {prof['max_total_bytes']} bytes")
    for rel, data in sorted(changed.items()):
        errors += path_errors(change_id, rel, prof)
        if data is None:
            if rel == tasks_path(change_id):
                errors.append("tasks.md must not be deleted")
            continue
        if b"\x00" in data:
            errors.append(f"{rel}: binary content")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{rel}: not UTF-8 text")
            continue
        if rel == tasks_path(change_id):
            old = (base_files.get(rel) or b"").decode("utf-8", "replace")
            if not tasks_only_checkboxes(old, text):
                errors.append("tasks.md may only change by ticking checkboxes")
    return errors


def prepare_patched_tree(base_dir, agent_dir, dest_dir, change_id, prof):
    """Validate the agent's tree against the pristine base and write base+changes into dest_dir.

    Returns the change set. Raises ImplRejected with every problem. dest_dir must not contain secrets:
    it is the tree the isolated verification job runs on.
    """
    changed, errs = diff_trees(base_dir, agent_dir)
    base_files, _ = snapshot_tree(base_dir)
    errs += check_changes(change_id, changed, base_files, prof)
    if errs:
        raise ImplRejected(errs)
    dest = Path(dest_dir)
    for rel, data in changed.items():
        target = dest / rel
        if data is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return changed


# --------------------------------------------------------------------------- gate and statuses
def evaluate_gate(rec, repo_full, cfg, permission, impl_pr):
    """Return a list of problems; empty means implementation may start for this repository."""
    problems = []
    allowed = {f"{cfg['owner']}/{n}" for n in cfg["allowed_repositories"]}
    short = repo_full.split("/")[-1]
    if repo_full not in allowed:
        problems.append(f"{repo_full} is not allowlisted")
    if profile(cfg, short) is None:
        problems.append(f"{repo_full} has no implementation profile in configs/repositories.json")
    if permission is not None and permission not in cfg["authorized_permissions"]:
        problems.append(f"requester permission {permission!r} is not authorized")
    status, reasons = p.compute_status(rec, allowed)
    if status not in (p.STATUS_READY, p.STATUS_IMPL_REVIEW):
        problems.append(f"status is {status!r}, not {p.STATUS_READY!r}"
                        + (": " + "; ".join(reasons) if reasons else ""))
    entry = next((r for r in rec.get("repositories", []) if r["name"] == repo_full), None)
    if entry is None:
        problems.append(f"{repo_full} is not an affected repository of this change")
        return problems
    if entry["pr_state"] != "merged" or entry.get("approved_revision") != rec.get("planning_revision"):
        problems.append("the plan for this repository is not merged and approved for the current revision")
    impl = rec.get("implementation") or {}
    for dep in entry.get("depends_on", []):
        if (impl.get(dep) or {}).get("pr_state") != "merged":
            problems.append(f"depends on {dep}, which is not implemented yet")
    if impl_pr and impl_pr["state"] == "OPEN":
        problems.append(f"implementation PR #{impl_pr['number']} is already open")
    if impl_pr and impl_pr["state"] == "MERGED":
        problems.append(f"already implemented by PR #{impl_pr['number']}")
    return problems


def observe_impl_prs(client, rec):
    seen = {}
    branch = implementation_branch(rec["change_id"])
    for name in (rec.get("implementation") or {}):
        pr = client.find_pr(name, branch)
        if pr:
            seen[name] = {"number": pr["number"], "state": "open" if pr["state"] == "OPEN" else "closed",
                          "merged": pr["state"] == "MERGED", "merge_commit_sha": pr.get("merge_commit_sha")}
    return seen


def apply_impl_pr_states(rec, prs):
    rec = copy.deepcopy(rec)
    for name, obs in prs.items():
        entry = (rec.get("implementation") or {}).get(name)
        if not entry:
            continue
        entry["pr"] = obs["number"]
        if obs.get("merged"):
            entry["pr_state"] = "merged"
            entry["merged_sha"] = obs.get("merge_commit_sha")
        else:
            entry["pr_state"] = "closed" if obs["state"] == "closed" else "open"
            entry["merged_sha"] = None
    return rec


def record_implementation(rec, repo_full, branch, pr_number, base_sha, verify, run_url):
    rec = copy.deepcopy(rec)
    rec.setdefault("implementation", {})[repo_full] = {
        "branch": branch, "pr": pr_number, "pr_state": "open", "merged_sha": None, "base_sha": base_sha,
        "verify": {"command": verify.get("command"), "exit_code": verify.get("exit_code")}, "run": run_url}
    return rec


def pr_title(rec, repo_full):
    return f"[ADLC impl] {rec['change_id']} ({repo_full.split('/')[1]})"


def pr_body(rec, repo_full, changed, verify, run_url, plan_entry):
    owner, srepo, snum = p.parse_story_ref(rec["story"])
    paths = sorted(changed)
    cmd = " ".join(verify.get("command") or [])
    lines = [
        f"## Implementation PR for `{rec['change_id']}`", "",
        "**Merging this PR accepts this repository's implementation.** It was produced by an AI implementer from the "
        "approved plan; review it like any other change. Nothing was merged or deployed automatically.", "",
        f"- Story: https://github.com/{owner}/{srepo}/issues/{snum} (`{rec['story']}`)",
        f"- Change ID: `{rec['change_id']}` (planning revision {rec['planning_revision']})",
        f"- Approved plan: PR #{plan_entry.get('plan_pr')} merged at `{(plan_entry.get('merged_sha') or '-')[:12]}`", "",
        "### Changed paths"] + [f"- `{x}`" for x in paths] + ["", "### Verification",
        f"- Command: `{cmd}`", f"- Exit code: {verify.get('exit_code')}",
        "- Run by a separate, isolated job on exactly this change set. The implementer's own statements about "
        "tests are not evidence.", "", "### Review checklist",
        "- Behavior matches the acceptance criteria and specs in `openspec/changes/" + rec["change_id"] + "/`",
        "- Tests cover every scenario; no unrelated changes", "- The repository's own CI passes on this PR"]
    if run_url:
        lines += ["", f"Workflow run: {run_url}"]
    return "\n".join(lines)


def result_comment_failure(rec, message, recovery):
    return (f"**ADLC implementation attempt failed**\n\n- Change ID: `{rec['change_id']}`\n- {message}\n"
            f"- Recovery: {recovery}\n")


def dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True)

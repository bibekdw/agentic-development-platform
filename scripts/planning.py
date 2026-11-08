#!/usr/bin/env python3
"""Deterministic coordination logic for epic/story OpenSpec planning.

Covers: story references, change IDs, coordination-record validation, allowed
changed paths, planning-change folder checks, contract-reference consistency,
idempotent PR planning, approval recording and readiness computation.
No network access; GitHub I/O lives in the workflows.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
STORY_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})#([1-9][0-9]{0,8})\Z")
CHANGE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}\Z")
PR_STATES = {"none", "open", "merged", "closed"}
VALIDATION = {"pending", "passed", "failed"}
MARKER = "adlc-coordination:v1"

STATUS_CLARIFY = "Needs clarification"
STATUS_PLANNING = "Planning"
STATUS_REVIEW = "Plan under review"
STATUS_FAILED = "Plan validation failed"
STATUS_READY = "Ready for implementation"
STATUS_BLOCKED = "Blocked by dependency"
STATUS_CANCELLED = "Cancelled"

REQUIRED_FILES = ("proposal.md", "design.md", "tasks.md")
# Coordination metadata a planning PR may touch besides its change folder.
PERMITTED_METADATA = ("openspec/changes/{cid}/",)


def parse_story_ref(ref):
    """'owner/repo#12' -> (owner, repo, 12). Unqualified numbers are rejected."""
    m = STORY_REF.fullmatch(ref) if isinstance(ref, str) else None
    if not m:
        raise ValueError(f"invalid story reference (must be owner/repo#number): {ref!r}")
    return m.group(1), m.group(2), int(m.group(3))


def make_change_id(repo, number, slug):
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    cid = f"{repo.lower()}-{number}-{slug}"[:64].rstrip("-")
    if not CHANGE_ID.fullmatch(cid):
        raise ValueError(f"cannot build valid change id from {repo!r}, {number!r}, {slug!r}")
    return cid


def planning_branch(change_id):
    return f"adlc/plan/{change_id}"


def source_hash(title, body):
    return hashlib.sha256(f"{title}\n{body or ''}".encode()).hexdigest()


def render_record_block(record):
    return f"<!-- {MARKER}\n{json.dumps(record, indent=2, sort_keys=True)}\n-->"


def parse_record_block(text):
    m = re.search(rf"<!-- {re.escape(MARKER)}\n(.*?)\n-->", text, re.S)
    if not m:
        raise ValueError("no coordination record block found")
    return json.loads(m.group(1))


def _ref_ok(ref):
    try:
        parse_story_ref(ref)
        return True
    except ValueError:
        return False


def validate_record(rec, allowed=None):
    """Return list of structural errors. `allowed` is a set of 'owner/repo' names."""
    e = []
    if not isinstance(rec, dict):
        return ["record must be an object"]
    if rec.get("schema_version") != SCHEMA_VERSION:
        e.append("unsupported schema_version")
    cid = rec.get("change_id")
    if not isinstance(cid, str) or not CHANGE_ID.fullmatch(cid):
        e.append("invalid change_id")
    story = rec.get("story")
    if not _ref_ok(story):
        e.append("story must be a fully qualified owner/repo#number")
    elif isinstance(cid, str):
        _, srepo, snum = parse_story_ref(story)
        if not cid.startswith(f"{srepo.lower()}-{snum}-"):
            e.append("change_id does not match source story")
    if rec.get("epic") is not None and not _ref_ok(rec["epic"]):
        e.append("epic must be null or a fully qualified reference")
    rev = rec.get("planning_revision")
    if type(rev) is not int or rev < 1:
        e.append("planning_revision must be a positive integer")
    if not isinstance(rec.get("source_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", rec["source_hash"]):
        e.append("source_hash must be a sha256 hex digest")
    for key in ("open_questions", "blocked_by", "runs"):
        if not isinstance(rec.get(key), list):
            e.append(f"{key} must be a list")
    if not isinstance(rec.get("cancelled"), bool):
        e.append("cancelled must be boolean")
    for i, b in enumerate(rec.get("blocked_by") or []):
        if not isinstance(b, dict) or not _ref_ok(b.get("story")) or not isinstance(b.get("ready"), bool):
            e.append(f"blocked_by[{i}] invalid")
    repos = rec.get("repositories")
    names = set()
    if not isinstance(repos, list) or not repos:
        e.append("repositories must be a non-empty list (complete affected-repository record)")
        repos = []
    for i, r in enumerate(repos):
        if not isinstance(r, dict):
            e.append(f"repositories[{i}] invalid")
            continue
        n = r.get("name")
        if not isinstance(n, str) or "/" not in n:
            e.append(f"repositories[{i}].name must be owner/repo")
            continue
        if n in names:
            e.append(f"repositories[{i}] duplicated")
        names.add(n)
        if allowed is not None and n not in allowed:
            e.append(f"repositories[{i}] not allowlisted")
        st = r.get("pr_state")
        if st not in PR_STATES:
            e.append(f"repositories[{i}].pr_state invalid")
        pr = r.get("plan_pr")
        if (st == "none") != (pr is None) or (pr is not None and (type(pr) is not int or pr < 1)):
            e.append(f"repositories[{i}] plan_pr inconsistent with pr_state")
        if r.get("validation") not in VALIDATION:
            e.append(f"repositories[{i}].validation invalid")
        if type(r.get("plan_revision")) is not int or r["plan_revision"] < 1:
            e.append(f"repositories[{i}].plan_revision invalid")
        if st == "merged":
            if not isinstance(r.get("merged_sha"), str) or not SHA40.fullmatch(r["merged_sha"]):
                e.append(f"repositories[{i}] merged without recorded commit SHA")
            if type(r.get("approved_revision")) is not int:
                e.append(f"repositories[{i}] merged without approved_revision")
        if not isinstance(r.get("contract_refs"), list):
            e.append(f"repositories[{i}].contract_refs must be a list")
    e.extend(check_contracts(rec, names))
    return e


def check_contracts(rec, repo_names=None):
    """Every contract has one authoritative owner among affected repos; refs resolve."""
    e = []
    contracts = rec.get("shared_contracts")
    if not isinstance(contracts, list):
        return ["shared_contracts must be a list"]
    names = repo_names if repo_names is not None else {r.get("name") for r in rec.get("repositories", [])}
    ids = {}
    for i, c in enumerate(contracts):
        if not isinstance(c, dict) or not {"id", "owner", "location"} <= set(c):
            e.append(f"shared_contracts[{i}] needs id, owner, location")
            continue
        if c["id"] in ids:
            e.append(f"contract {c['id']} has more than one definition")
        ids[c["id"]] = c
        if c["owner"] not in names:
            e.append(f"contract {c['id']} owner {c['owner']} is not an affected repository")
    for r in rec.get("repositories", []):
        for ref in r.get("contract_refs", []) if isinstance(r, dict) else []:
            if ref not in ids:
                e.append(f"{r.get('name')} references undefined contract {ref}")
    for cid, c in ids.items():
        owner = next((r for r in rec.get("repositories", []) if r.get("name") == c["owner"]), None)
        if owner and cid not in owner.get("contract_refs", []):
            e.append(f"owner {c['owner']} does not list its own contract {cid}")
    return e


def compute_status(rec, allowed=None):
    """Return (status, reasons). Readiness never relies on AI judgement."""
    if isinstance(rec, dict) and rec.get("cancelled") is True:
        return STATUS_CANCELLED, ["story planning cancelled"]
    errors = validate_record(rec, allowed)
    if errors:
        return STATUS_FAILED, errors
    if rec["open_questions"]:
        return STATUS_CLARIFY, list(rec["open_questions"])
    repos = rec["repositories"]
    if any(r["validation"] == "failed" for r in repos):
        return STATUS_FAILED, [f"{r['name']}: validation failed" for r in repos if r["validation"] == "failed"]
    waiting = [b["story"] for b in rec["blocked_by"] if not b["ready"]]
    if waiting:
        return STATUS_BLOCKED, [f"waiting for {s}" for s in waiting]
    rev = rec["planning_revision"]
    missing = []
    for r in repos:
        n = r["name"]
        if r["pr_state"] == "none":
            missing.append(f"{n}: planning PR not created")
        elif r["pr_state"] == "closed":
            missing.append(f"{n}: planning PR #{r['plan_pr']} closed without merge; reconcile or re-plan")
        elif r["pr_state"] == "merged" and r["approved_revision"] != rev:
            missing.append(f"{n}: approval covers revision {r['approved_revision']}, current is {rev}; re-approval required")
    if missing:
        return STATUS_PLANNING, missing
    unmerged = [r for r in repos if r["pr_state"] == "open"]
    if unmerged:
        return STATUS_REVIEW, [f"{r['name']}: PR #{r['plan_pr']} awaiting merge" for r in unmerged]
    pending = [r["name"] for r in repos if r["validation"] != "passed"]
    if pending:
        return STATUS_REVIEW, [f"{n}: validation not yet passed" for n in pending]
    return STATUS_READY, []


def apply_pr_states(rec, prs):
    """Refresh the record from observed PR states.

    prs: {"owner/repo": {"number": int, "state": "open|closed", "merged": bool,
                         "merge_commit_sha": str|None}}
    Merging records approval for the revision the PR carried (`plan_revision`).
    """
    rec = json.loads(json.dumps(rec))
    for r in rec["repositories"]:
        obs = prs.get(r["name"])
        if not obs:
            continue
        r["plan_pr"] = obs["number"]
        if obs.get("merged"):
            r["pr_state"] = "merged"
            r["merged_sha"] = obs.get("merge_commit_sha")
            r["approved_revision"] = r["plan_revision"]
        else:
            r["pr_state"] = "closed" if obs["state"] == "closed" else "open"
            r["merged_sha"] = None
            r.pop("approved_revision", None)
    return rec


def bump_revision(rec, new_hash):
    """New source snapshot -> new planning revision; prior approvals stop counting."""
    if new_hash == rec["source_hash"]:
        return rec
    rec = json.loads(json.dumps(rec))
    rec["planning_revision"] += 1
    rec["source_hash"] = new_hash
    for r in rec["repositories"]:
        r["validation"] = "pending"
    return rec


def plan_actions(rec, existing_prs):
    """Idempotent retry plan. existing_prs: {"owner/repo": pr_number} found by branch."""
    actions = []
    for r in rec["repositories"]:
        found = existing_prs.get(r["name"]) or r.get("plan_pr")
        if r["pr_state"] == "merged" and r.get("approved_revision") == rec["planning_revision"]:
            actions.append({"repo": r["name"], "action": "skip-approved"})
        elif found:
            action = "update-existing" if r["plan_revision"] != rec["planning_revision"] else "reuse"
            actions.append({"repo": r["name"], "action": action, "pr": found})
        else:
            actions.append({"repo": r["name"], "action": "create"})
    return actions


def check_changed_paths(change_id, paths):
    """Planning PRs may change only the change folder (plus permitted metadata)."""
    allowed = tuple(p.format(cid=change_id) for p in PERMITTED_METADATA)
    bad = []
    for p in paths:
        norm = p.replace("\\", "/")
        if ".." in norm.split("/") or norm.startswith("/") or not norm.startswith(allowed):
            bad.append(p)
    return [f"path not permitted in planning PR: {p}" for p in bad]


PROPOSAL_HEADINGS = ("Why", "What Changes", "Capabilities")
TASK_ITEM = re.compile(r"^- \[[ xX]\] \d+\.\d+ \S", re.M)
TASK_ANY_BOX = re.compile(r"^\s*- \[[ xX]\]", re.M)
TASK_NUMBERED_HEADING = re.compile(r"^#{1,6}\s*\d+\.\d+", re.M)


def check_tasks_format(text):
    """OpenSpec task format: '## 1. Group' headings with numbered '- [ ] 1.1 item' checkboxes."""
    e = []
    if not TASK_ITEM.search(text):
        e.append("tasks.md has no numbered checklist items like '- [ ] 1.1 description'")
    if len(TASK_ANY_BOX.findall(text)) != len(TASK_ITEM.findall(text)):
        e.append("tasks.md has checkboxes without numbering ('- [ ] 1.1 ...')")
    if TASK_NUMBERED_HEADING.search(text):
        e.append("tasks.md uses numbered items as headings; use '## 1. Group' headings and '- [ ] 1.1' items")
    return e


def validate_change_folder(root, change_id, story, contract_refs=(), epic=None):
    """Deterministic OpenSpec-structure and traceability checks for one repository."""
    root = Path(root)
    base = root / "openspec" / "changes" / change_id
    e = []
    if not base.is_dir():
        return [f"missing {base.as_posix()}"]
    texts = {}
    for f in REQUIRED_FILES:
        p = base / f
        if not p.is_file() or not p.read_text(encoding="utf-8").strip():
            e.append(f"missing or empty {f}")
        else:
            texts[f] = p.read_text(encoding="utf-8")
    specs = list((base / "specs").glob("*/spec.md")) if (base / "specs").is_dir() else []
    if not specs:
        e.append("no delta specifications under specs/<capability>/spec.md")
    for s in specs:
        t = s.read_text(encoding="utf-8")
        if not re.search(r"^## (ADDED|MODIFIED|REMOVED|RENAMED) Requirements", t, re.M):
            e.append(f"{s.name}: not a delta spec")
        if "#### Scenario:" not in t:
            e.append(f"{s.parent.name}: no acceptance scenario")
    proposal = texts.get("proposal.md", "")
    for heading in PROPOSAL_HEADINGS:
        if "proposal.md" in texts and not re.search(rf"^##\s+{heading}\s*$", proposal, re.M):
            e.append(f"proposal.md is missing the '## {heading}' section")
    for s in specs:
        if "proposal.md" in texts and s.parent.name not in proposal:
            e.append(f"proposal.md does not list capability '{s.parent.name}' under Capabilities")
    if "tasks.md" in texts:
        e.extend(check_tasks_format(texts["tasks.md"]))
    if story not in proposal:
        e.append("proposal.md does not reference the source story")
    if epic and epic not in proposal:
        e.append("proposal.md does not reference the epic")
    if change_id not in proposal:
        e.append("proposal.md does not state the change id")
    combined = proposal + texts.get("design.md", "")
    for ref in contract_refs:
        if ref not in combined:
            e.append(f"missing reference to shared contract {ref}")
    if (root / "openspec" / "specs").is_dir() and base.parent.parent.name != "openspec":
        e.append("unexpected layout")
    return e


PLAN_META = "adlc-plan.json"


def plan_metadata(rec, entry, depends_on=()):
    """Machine-written metadata stored inside each change folder (never agent-authored)."""
    return {"change_id": rec["change_id"], "story": rec["story"], "epic": rec.get("epic"),
            "planning_revision": rec["planning_revision"], "repository": entry["name"],
            "contract_refs": sorted(entry["contract_refs"]), "depends_on": sorted(depends_on)}


def validate_plan_metadata(root, change_id, repository=None):
    """Return (errors, meta) for openspec/changes/<id>/adlc-plan.json."""
    path = Path(root) / "openspec" / "changes" / change_id / PLAN_META
    if not path.is_file():
        return [f"missing {PLAN_META}"], None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [f"{PLAN_META} is not valid JSON"], None
    e = []
    if not isinstance(meta, dict) or set(meta) != {"change_id", "story", "epic", "planning_revision",
                                                   "repository", "contract_refs", "depends_on"}:
        return [f"{PLAN_META} has unexpected or missing fields"], None
    if meta["change_id"] != change_id:
        e.append(f"{PLAN_META} change_id does not match folder")
    if not _ref_ok(meta["story"]):
        e.append(f"{PLAN_META} story is not a fully qualified reference")
    if meta["epic"] is not None and not _ref_ok(meta["epic"]):
        e.append(f"{PLAN_META} epic is not a fully qualified reference")
    if type(meta["planning_revision"]) is not int or meta["planning_revision"] < 1:
        e.append(f"{PLAN_META} planning_revision invalid")
    if repository is not None and meta["repository"] != repository:
        e.append(f"{PLAN_META} repository {meta['repository']} != {repository}")
    for key in ("contract_refs", "depends_on"):
        if not isinstance(meta[key], list) or not all(isinstance(x, str) for x in meta[key]):
            e.append(f"{PLAN_META} {key} must be a list of strings")
    return e, meta


def cross_check_plans(rec, metas):
    """Explicit cross-repository consistency: same id/story/revision, refs match the record.

    metas: {"owner/repo": meta}. This proves reference consistency only, not semantic
    compatibility of contract content.
    """
    e = []
    for r in rec["repositories"]:
        m = metas.get(r["name"])
        if m is None:
            e.append(f"{r['name']}: no plan metadata to compare")
            continue
        for key in ("change_id", "story", "epic", "planning_revision"):
            if m.get(key) != rec.get(key):
                e.append(f"{r['name']}: {key} {m.get(key)!r} differs from record {rec.get(key)!r}")
        if sorted(m.get("contract_refs", [])) != sorted(r["contract_refs"]):
            e.append(f"{r['name']}: contract_refs differ from coordination record")
    e.extend(check_contracts(rec))
    return e


def render_status_comment(rec, status, reasons):
    lines = [f"**ADLC planning status: {status}**", "",
             f"- Change ID: `{rec['change_id']}` (revision {rec['planning_revision']})",
             f"- Story: {rec['story']}" + (f" | Epic: {rec['epic']}" if rec.get("epic") else ""), ""]
    for r in rec["repositories"]:
        pr = f"#{r['plan_pr']}" if r.get("plan_pr") else "-"
        sha = f" @ `{r['merged_sha'][:12]}`" if r.get("merged_sha") else ""
        lines.append(f"- {r['name']}: PR {pr} {r['pr_state']}{sha}")
    if reasons:
        lines += ["", "Outstanding:"] + [f"- {x}" for x in reasons]
    if rec.get("last_failure"):
        lf = rec["last_failure"]
        lines += ["", f"Last failure: {lf.get('message')} (recovery: {lf.get('recovery')})"]
    lines += ["", "Merging a planning PR approves the plan, not the implementation."]
    return "\n".join(lines)


def _cli():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("record", type=Path)
    s.add_argument("--prs", type=Path, help="observed PR states JSON")
    s.add_argument("--allowlist", type=Path, default=Path("configs/repositories.json"))
    s.add_argument("--out", type=Path, help="write refreshed record here")
    c = sub.add_parser("check-paths")
    c.add_argument("change_id")
    c.add_argument("paths", nargs="+")
    v = sub.add_parser("validate-change")
    v.add_argument("root", type=Path)
    v.add_argument("change_id")
    v.add_argument("--story", required=True)
    v.add_argument("--epic")
    v.add_argument("--contract", action="append", default=[])
    a = ap.parse_args()
    if a.cmd == "status":
        cfg = json.loads(a.allowlist.read_text())
        rec = json.loads(a.record.read_text())
        owner = cfg.get("owner", "")
        allowed = {f"{owner}/{n}" for n in cfg["allowed_repositories"]}
        if a.prs:
            rec = apply_pr_states(rec, json.loads(a.prs.read_text()))
        status, reasons = compute_status(rec, allowed)
        if a.out:
            a.out.write_text(json.dumps(rec, indent=2, sort_keys=True))
        print(json.dumps({"status": status, "reasons": reasons}))
        return 0 if status != STATUS_FAILED else 1
    errs = (check_changed_paths(a.change_id, a.paths) if a.cmd == "check-paths"
            else validate_change_folder(a.root, a.change_id, a.story, a.contract, a.epic))
    for m in errs:
        print("ERROR:", m)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(_cli())

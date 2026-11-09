#!/usr/bin/env python3
"""Refresh planning PR states, recompute readiness, update records.

Usage:
  reconcile.py <platform-owner/repo> <change_id> [--dry-run]    one change
  reconcile.py <platform-owner/repo> --all [--dry-run]          every open coordination issue

Reads PR states, records approvals (merged SHA + revision), recomputes the deterministic
status and updates the tracking issue and the single story status comment. It never merges,
closes or edits planning PRs. `--all` skips changes that are already Implemented, cancelled, or Ready with no implementation started, only writes when something changed, and keeps going if one change fails.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planning as p  # noqa: E402
import implementation as impl  # noqa: E402
from ghclient import GitHubClient, GitHubError, observe_prs  # noqa: E402
from publish_plan import tracking_body  # noqa: E402

TERMINAL = (p.STATUS_IMPLEMENTED, p.STATUS_CANCELLED)


def _allowed(cfg):
    return {f"{cfg['owner']}/{n}" for n in cfg["allowed_repositories"]}


def _reconcile_issue(client, cfg, platform, issue, change_id, dry_run):
    rec = p.parse_record_block(issue["body"])
    if rec["change_id"] != change_id:
        raise SystemExit("coordination record does not match the requested change id")
    rec = p.apply_pr_states(rec, observe_prs(client, rec))
    if rec.get("implementation"):
        rec = impl.apply_impl_pr_states(rec, impl.observe_impl_prs(client, rec))
    status, reasons = p.compute_status(rec, _allowed(cfg))
    if rec["open_questions"] and status != p.STATUS_CANCELLED:
        status, reasons = p.STATUS_CLARIFY, rec["open_questions"]
    comment = (p.render_status_comment(rec, status, reasons) if status != p.STATUS_FAILED else
               f"**ADLC planning status: {status}**\n\n" + "\n".join(f"- {x}" for x in reasons))
    if not dry_run:
        body = tracking_body(rec, status, reasons)
        if body != issue["body"]:
            client.update_tracking_issue(platform, issue["number"], body)
        owner, repo, number = p.parse_story_ref(rec["story"])
        client.upsert_status_comment(f"{owner}/{repo}", number, f"<!-- adlc-status:{change_id} -->", comment)
    return {"change_id": change_id, "status": status, "reasons": reasons, "record": rec}


def reconcile(client, cfg, platform, change_id, dry_run=False):
    issue = client.find_tracking_issue(platform, change_id=change_id)
    if not issue:
        raise SystemExit(f"no coordination issue for {change_id}")
    return _reconcile_issue(client, cfg, platform, issue, change_id, dry_run)


def reconcile_all(client, cfg, platform, dry_run=False):
    """Reconcile every open coordination issue. One bad issue never stops the others."""
    results, failures = [], []
    for issue in client.list_open_tracking_issues(platform):
        label = f"#{issue['number']}"
        try:
            rec = p.parse_record_block(issue["body"])
            change_id = rec["change_id"]
            label = change_id
            stored, _ = p.compute_status(rec, _allowed(cfg))
            if stored in TERMINAL or (stored == p.STATUS_READY and not rec.get("implementation")):
                results.append({"change_id": change_id, "status": stored, "skipped": True})
                continue
            res = _reconcile_issue(client, cfg, platform, issue, change_id, dry_run)
            results.append({k: v for k, v in res.items() if k != "record"})
        except (ValueError, KeyError, TypeError, GitHubError, SystemExit) as exc:
            failures.append({"change": label, "error": str(exc)[:200]})
    return {"results": results, "failures": failures}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("platform")
    ap.add_argument("change_id", nargs="?", default="")
    ap.add_argument("--all", action="store_true", help="reconcile every open coordination issue")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if bool(a.change_id) == a.all:
        ap.error("give exactly one of <change_id> or --all")
    cfg = json.loads((Path(__file__).resolve().parents[1] / "configs" / "repositories.json").read_text())
    client = GitHubClient()
    if a.all:
        out = reconcile_all(client, cfg, a.platform, a.dry_run)
        print(json.dumps(out))
        return 1 if out["failures"] else 0
    res = reconcile(client, cfg, a.platform, a.change_id, a.dry_run)
    print(json.dumps({k: v for k, v in res.items() if k != "record"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

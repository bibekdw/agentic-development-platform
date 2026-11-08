#!/usr/bin/env python3
"""Refresh planning PR states for one change, recompute readiness, update records.

Usage: reconcile.py <platform-owner/repo> <change_id> [--dry-run]
Reads PR states, records approvals (merged SHA + revision), recomputes the deterministic
status and updates the tracking issue and the single story status comment. It never merges,
closes or edits planning PRs.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planning as p  # noqa: E402
from ghclient import GitHubClient, observe_prs  # noqa: E402
from publish_plan import tracking_body  # noqa: E402


def reconcile(client, cfg, platform, change_id, dry_run=False):
    issue = client.find_tracking_issue(platform, change_id=change_id)
    if not issue:
        raise SystemExit(f"no coordination issue for {change_id}")
    rec = p.parse_record_block(issue["body"])
    if rec["change_id"] != change_id:
        raise SystemExit("coordination record does not match the requested change id")
    allowed = {f"{cfg['owner']}/{n}" for n in cfg["allowed_repositories"]}
    rec = p.apply_pr_states(rec, observe_prs(client, rec))
    status, reasons = p.compute_status(rec, allowed)
    if rec["open_questions"] and status != p.STATUS_CANCELLED:
        status, reasons = p.STATUS_CLARIFY, rec["open_questions"]
    comment = (p.render_status_comment(rec, status, reasons) if status != p.STATUS_FAILED else
               f"**ADLC planning status: {status}**\n\n" + "\n".join(f"- {x}" for x in reasons))
    if not dry_run:
        client.update_tracking_issue(platform, issue["number"], tracking_body(rec, status, reasons))
        owner, repo, number = p.parse_story_ref(rec["story"])
        client.upsert_status_comment(f"{owner}/{repo}", number, f"<!-- adlc-status:{change_id} -->", comment)
    return {"change_id": change_id, "status": status, "reasons": reasons, "record": rec}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("platform")
    ap.add_argument("change_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads((Path(__file__).resolve().parents[1] / "configs" / "repositories.json").read_text())
    res = reconcile(GitHubClient(), cfg, a.platform, a.change_id, a.dry_run)
    print(json.dumps({k: v for k, v in res.items() if k != "record"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

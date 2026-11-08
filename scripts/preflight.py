#!/usr/bin/env python3
"""Pre-planning gate. Pure evaluation plus a thin `gh api` CLI wrapper.

Issue text is treated as data only: it is parsed for structure and never executed
or allowed to influence permissions or policy.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from planning import parse_story_ref  # noqa: E402

AC_HEADING = re.compile(r"^#{1,6}\s*acceptance criteria\s*$", re.M | re.I)
ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?:\[[ xX]\]\s*)?\S.{3,}", re.M)


def acceptance_criteria(body):
    m = AC_HEADING.search(body or "")
    if not m:
        return []
    rest = body[m.end():]
    nxt = re.search(r"^#{1,6}\s", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    return [x.strip() for x in ITEM.findall(section)]


def evaluate(story_ref, issue, permission, cfg):
    """Return (ok, problems, questions). problems abort; questions => Needs clarification."""
    problems, questions = [], []
    try:
        owner, repo, _ = parse_story_ref(story_ref)
    except ValueError as exc:
        return False, [str(exc)], []
    if owner != cfg["owner"] or repo not in cfg["allowed_repositories"]:
        problems.append(f"{owner}/{repo} is not allowlisted")
    if permission not in cfg["authorized_permissions"]:
        problems.append(f"requester permission {permission!r} is not authorized")
    if not issue or issue.get("state") != "open":
        problems.append("issue does not exist or is not open")
    if issue and "pull_request" in issue:
        problems.append("reference is a pull request, not a story issue")
    if problems:
        return False, problems, []
    if not acceptance_criteria(issue.get("body")):
        questions.append("Add an 'Acceptance criteria' section with observable, testable items.")
    return not questions, [], questions


def _gh(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def post_clarification(story_ref, issue, questions, platform):
    """Post the single consolidated status comment (Needs clarification). Requires a write token."""
    import planning as p
    from ghclient import GitHubClient
    client = GitHubClient()
    owner, repo, number = parse_story_ref(story_ref)
    tracking = client.find_tracking_issue(platform, story_ref=story_ref)
    cid = p.parse_record_block(tracking["body"])["change_id"] if tracking else \
        p.make_change_id(repo, number, issue["title"])
    body = ("**ADLC planning status: Needs clarification**\n\n"
            + "\n".join(f"- {q}" for q in questions)
            + "\n\nUpdate the story, then run the planning workflow again.")
    client.upsert_status_comment(f"{owner}/{repo}", number, f"<!-- adlc-status:{cid} -->", body)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("story")
    ap.add_argument("actor")
    ap.add_argument("--platform", default="")
    ap.add_argument("--comment", action="store_true", help="post questions on the story (needs write token)")
    a = ap.parse_args(argv)
    cfg = json.loads((Path(__file__).resolve().parents[1] / "configs" / "repositories.json").read_text())
    try:
        owner, repo, num = parse_story_ref(a.story)
    except ValueError as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)], "questions": []}))
        return 1
    issue = _gh(f"repos/{owner}/{repo}/issues/{num}")
    perm = (_gh(f"repos/{owner}/{repo}/collaborators/{a.actor}/permission") or {}).get("permission")
    ok, problems, questions = evaluate(a.story, issue, perm, cfg)
    result = {"ok": ok, "problems": problems, "questions": questions}
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"ok={str(ok).lower()}\nneeds_clarification={str(bool(questions)).lower()}\n")
    Path("preflight.json").write_text(json.dumps(result))
    print(json.dumps(result))
    if questions and a.comment:
        post_clarification(a.story, issue, questions, a.platform)
    return 0 if ok or questions else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Thin GitHub/Git I/O layer used by publish and reconcile.

Everything the planner machinery does against GitHub goes through this class so it can be
replaced by a fake in tests. Credentials come only from the GH_TOKEN environment variable
and are never written to disk, logs or generated artifacts.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

LABEL = "adlc-coordination"
GIT_ENV_IDENTITY = {"GIT_AUTHOR_NAME": "adlc-planner", "GIT_AUTHOR_EMAIL": "adlc-planner@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": "adlc-planner", "GIT_COMMITTER_EMAIL": "adlc-planner@users.noreply.github.com"}


class GitHubError(RuntimeError):
    pass


def pick_pr(prs):
    """Prefer a merged PR, then open, then the newest closed one."""
    for state in ("MERGED", "OPEN", "CLOSED"):
        match = [x for x in prs if x["state"] == state]
        if match:
            return max(match, key=lambda x: x["number"])
    return None


def match_tracking(issues, story_ref=None, change_id=None):
    """Title format is '[ADLC plan] <story> <change-id>'; match whole tokens so #12 never matches #123."""
    found = [i for i in issues if (story_ref and f" {story_ref} " in i["title"])
             or (change_id and i["title"].endswith(f" {change_id}"))]
    if len(found) > 1:
        raise GitHubError(f"multiple coordination issues match: {[i['number'] for i in found]}")
    return found[0] if found else None


def observe_prs(client, rec):
    """Observed planning-PR states for every repository in the record."""
    import planning
    branch = planning.planning_branch(rec["change_id"])
    seen = {}
    for r in rec["repositories"]:
        pr = client.find_pr(r["name"], branch)
        if pr:
            seen[r["name"]] = {"number": pr["number"], "state": "open" if pr["state"] == "OPEN" else "closed",
                               "merged": pr["state"] == "MERGED", "merge_commit_sha": pr.get("merge_commit_sha")}
    return seen


def git(cwd, *args, env=None, check=True):
    full = dict(os.environ, **GIT_ENV_IDENTITY, GIT_TERMINAL_PROMPT="0")
    if env:
        full.update(env)
    out = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, env=full, capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    if check and out.returncode != 0:
        raise GitHubError(f"git {args[0]} failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


class GitHubClient:
    def __init__(self):
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise GitHubError("GH_TOKEN is not set")
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        print(f"::add-mask::{basic}")  # the encoded form is not auto-masked by Actions
        self._git_env = {"GIT_CONFIG_COUNT": "1",
                         "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                         "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}

    def _gh(self, *args, stdin=None, check=True):
        out = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin,
                             stdin=None if stdin is not None else subprocess.DEVNULL)
        if check and out.returncode != 0:
            raise GitHubError(f"gh {' '.join(args[:2])} failed: {out.stderr.strip()[:300]}")
        return out.stdout

    def _api(self, path, *extra):
        return json.loads(self._gh("api", path, *extra))

    # ---- reads
    def get_issue(self, repo, number):
        out = subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}"], capture_output=True, text=True)
        return json.loads(out.stdout) if out.returncode == 0 else None

    def get_permission(self, repo, user):
        out = subprocess.run(["gh", "api", f"repos/{repo}/collaborators/{user}/permission"],
                             capture_output=True, text=True)
        return json.loads(out.stdout).get("permission") if out.returncode == 0 else None

    def default_branch(self, repo):
        return self._api(f"repos/{repo}")["default_branch"]

    def get_comments(self, repo, number, limit=20):
        out = subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}/comments?per_page={limit}"],
                             capture_output=True, text=True)
        return json.loads(out.stdout) if out.returncode == 0 else []

    def find_pr(self, repo, branch):
        data = json.loads(self._gh("pr", "list", "-R", repo, "--head", branch, "--state", "all",
                                   "--json", "number,state,url,mergeCommit"))
        pr = pick_pr(data)
        if not pr:
            return None
        return {"number": pr["number"], "state": pr["state"], "url": pr["url"],
                "merge_commit_sha": (pr.get("mergeCommit") or {}).get("oid")}

    def find_tracking_issue(self, platform, story_ref=None, change_id=None):
        """Find the single coordination issue for a story or change id (exact title-token match)."""
        key = story_ref or change_id
        data = json.loads(self._gh("issue", "list", "-R", platform, "--label", LABEL, "--state", "all",
                                   "--search", f'"{key}" in:title', "--json", "number,title,body", "--limit", "50"))
        return match_tracking(data, story_ref, change_id)

    # ---- writes (never called in dry-run)
    def create_tracking_issue(self, platform, title, body):
        self._gh("label", "create", LABEL, "-R", platform, "--force",
                 "--description", "ADLC coordination record", check=False)
        url = self._gh("issue", "create", "-R", platform, "--title", title, "--label", LABEL,
                       "--body-file", "-", stdin=body).strip()
        return int(url.rstrip("/").rsplit("/", 1)[1])

    def update_tracking_issue(self, platform, number, body):
        self._gh("issue", "edit", str(number), "-R", platform, "--body-file", "-", stdin=body)

    def upsert_status_comment(self, repo, number, marker, body):
        comments = json.loads(self._gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate"))
        mine = [c for c in comments if marker in c["body"]]
        payload = f"{marker}\n{body}"
        if mine:
            self._gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{mine[0]['id']}", "-f", f"body={payload}")
        else:
            self._gh("issue", "comment", str(number), "-R", repo, "--body-file", "-", stdin=payload)

    def create_pr(self, repo, branch, base, title, body):
        url = self._gh("pr", "create", "-R", repo, "--head", branch, "--base", base, "--title", title,
                       "--body-file", "-", "--draft", stdin=body).strip().splitlines()[-1]
        return {"number": int(url.rstrip("/").rsplit("/", 1)[1]), "url": url}

    def update_pr_body(self, repo, number, body):
        self._gh("pr", "edit", str(number), "-R", repo, "--body-file", "-", stdin=body)

    # ---- git
    def clone(self, repo, dest, depth=None):
        # No credentials are stored in the clone: the auth header lives only in the process environment.
        extra = ["--depth", str(depth)] if depth else []
        git(".", "clone", "--quiet", *extra, f"https://github.com/{repo}.git", str(dest), env=self._git_env)
        return Path(dest)

    def git_env(self):
        return self._git_env


if __name__ == "__main__":
    print("library module", file=sys.stderr)

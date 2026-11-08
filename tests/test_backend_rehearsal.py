"""Rehearsal of acceptance demo A against a LOCAL copy of the real backend repository.

Set ADLC_BACKEND_REPO to the path of a local adlc-sample-backend working tree that already contains
the OpenSpec setup (openspec/config.yaml, planning-pr-validate workflow). The test copies the working
tree (including uncommitted setup files) into a throwaway bare remote, runs the real publish pipeline
with the real pinned OpenSpec CLI, then runs the installed workflow's validator command on the
resulting planning branch. Nothing leaves the machine. Skipped when the variable is unset.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import planning as p
import test_publish as tp
from ghclient import git

SRC = os.environ.get("ADLC_BACKEND_REPO")
TITLE = "Reject task titles longer than 200 characters"


def write_backend_plan(plan_dir, ref, cid):
    base = Path(plan_dir) / "adlc-sample-backend" / "openspec" / "changes" / cid
    (base / "specs" / "task-titles").mkdir(parents=True)
    (Path(plan_dir) / "plan.json").write_text(__import__("json").dumps({
        "story": ref, "epic": None, "summary": "Validate task title length in the backend.", "open_questions": [],
        "affected_repositories": [{"name": "adlc-sample-backend", "scope": "Validate title length on task creation",
                                   "contract_refs": [], "depends_on": []}],
        "shared_contracts": []}))
    (base / "proposal.md").write_text(
        f"## Why\nTask titles are unbounded. Source story: {ref}. Change id: {cid}.\n\n"
        "## What Changes\n- Reject titles longer than 200 characters and blank titles on task creation.\n\n"
        "## Capabilities\n### New Capabilities\n- `task-titles`: title length limit on task creation\n\n"
        "## Impact\n- Affected: backend task API only. No other repository changes. Out of scope: UI messaging.\n")
    (base / "design.md").write_text("## Context\nTask creation currently accepts any title.\n\n"
                                    "## Decisions\n- Reject with HTTP 400; limit is 200 characters inclusive.\n")
    (base / "tasks.md").write_text("## 1. Implementation\n- [ ] 1.1 Add title validation\n\n"
                                   "## 2. Tests\n- [ ] 2.1 200 accepted\n- [ ] 2.2 201 rejected\n- [ ] 2.3 blank rejected\n")
    (base / "specs" / "task-titles" / "spec.md").write_text(
        "## ADDED Requirements\n### Requirement: Task title length limit\n"
        "The system SHALL reject task titles longer than 200 characters and blank titles.\n\n"
        "#### Scenario: 200 characters accepted\n- **WHEN** a task is created with a 200-character title\n"
        "- **THEN** the task is created\n\n"
        "#### Scenario: 201 characters rejected\n- **WHEN** a task is created with a 201-character title\n"
        "- **THEN** the request is rejected with HTTP 400\n\n"
        "#### Scenario: Blank title rejected\n- **WHEN** a task is created with a blank title\n"
        "- **THEN** the request is rejected with HTTP 400\n")


@unittest.skipUnless(SRC and Path(SRC, "openspec", "config.yaml").is_file(), "ADLC_BACKEND_REPO not set / not set up")
class BackendRehearsal(tp.Base):
    def setUp(self):
        super().setUp()
        # replace the generic fake backend with a copy of the real working tree
        seed = self.tmp / "backend-seed"
        shutil.copytree(SRC, seed, ignore=shutil.ignore_patterns(".git", "target"))
        git(".", "init", "-q", "-b", "main", str(seed))
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "snapshot")
        bare = self.tmp / "backend-real.git"
        git(".", "clone", "-q", "--bare", str(seed), str(bare))
        self.c.remotes[tp.BACKEND] = bare
        self.main_files = set(git(bare, "ls-tree", "-r", "--name-only", "main").splitlines())

    def test_demo_a_one_repository_story(self):
        ref, cid = self.backend_story()
        write_backend_plan(self.plan, ref, cid)
        res = self.run_publish(ref, cli=True)  # real OpenSpec CLI
        self.assertEqual(res["failures"], [], res["failures"])
        self.assertEqual(res["status"], p.STATUS_REVIEW)
        branch = p.planning_branch(cid)
        added = set(self.c.remote_files(tp.BACKEND, branch)) - self.main_files
        self.assertTrue(all(f.startswith(f"openspec/changes/{cid}/") for f in added), added)
        self.assertFalse(any(f.startswith("src/") or f == "pom.xml" for f in added))  # no application code
        # the installed workflow's exact validator command, on the published branch
        co = self.tmp / "co"
        git(".", "clone", "-q", str(self.c.remotes[tp.BACKEND]), str(co))
        git(co, "checkout", "-q", branch)
        out = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "validate_planning_pr.py"),
                              "--root", str(co), "--branch", branch, "--base-ref", "origin/main",
                              "--repository", tp.BACKEND], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)
        # approval by manual merge + reconcile -> Ready
        import reconcile as rc
        self.c.set_pr(tp.BACKEND, branch, "MERGED", "c" * 40)
        self.assertEqual(rc.reconcile(self.c, tp.CFG, tp.PLATFORM, cid)["status"], p.STATUS_READY)

    def test_validator_rejects_application_code_change_on_planning_branch(self):
        ref, cid = self.backend_story()
        write_backend_plan(self.plan, ref, cid)
        self.run_publish(ref, cli=False)
        branch = p.planning_branch(cid)
        co = self.tmp / "co2"
        git(".", "clone", "-q", str(self.c.remotes[tp.BACKEND]), str(co))
        git(co, "checkout", "-q", branch)
        (co / "src").mkdir(exist_ok=True)
        (co / "src" / "Sneaky.java").write_text("class Sneaky {}")
        git(co, "add", "-A")
        git(co, "commit", "-q", "-m", "sneaky")
        out = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "validate_planning_pr.py"),
                              "--root", str(co), "--branch", branch, "--base-ref", "origin/main",
                              "--repository", tp.BACKEND, "--no-openspec"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("path not permitted", out.stdout)


if __name__ == "__main__":
    unittest.main()

"""Tests that execute the REAL pinned OpenSpec CLI (needs node/npx and the npm registry).

Skipped automatically when npx is unavailable or ADLC_SKIP_OPENSPEC_CLI=1.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import planning as p
import test_publish as tp
import validate_planning_pr as vp

HAVE_NPX = bool(shutil.which("npx") or shutil.which("npx.cmd")) and os.environ.get("ADLC_SKIP_OPENSPEC_CLI") != "1"


@unittest.skipUnless(HAVE_NPX, "npx not available")
class PinnedOpenSpecCli(unittest.TestCase):
    cid = "adlc-sample-backend-12-title-length"

    def make(self, root, cid, scenario=True, design=True):
        base = Path(root) / "openspec" / "changes" / cid
        (base / "specs" / "task-titles").mkdir(parents=True)
        (base / "proposal.md").write_text("## Why\nx\n\n## What Changes\n- y\n\n## Impact\n- z\n")
        if design:
            (base / "design.md").write_text("## Context\nx\n")
        (base / "tasks.md").write_text("## 1. Do\n- [ ] 1.1 a\n")
        sc = "#### Scenario: ok\n- **WHEN** a\n- **THEN** b\n" if scenario else ""
        (base / "specs" / "task-titles" / "spec.md").write_text(
            f"## ADDED Requirements\n### Requirement: Limit\nThe system SHALL limit titles.\n\n{sc}")

    def test_valid_invalid_missing_and_scope(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "openspec").mkdir()
            (Path(d) / "openspec" / "config.yaml").write_text("schema: spec-driven\n")
            self.make(d, self.cid)
            self.make(d, "adlc-sample-backend-13-bad", scenario=False)
            ok, out = vp.run_openspec(d, self.cid)
            self.assertTrue(ok, out)
            # machine metadata inside the change folder must not break validation
            (Path(d) / "openspec" / "changes" / self.cid / p.PLAN_META).write_text("{}")
            ok, out = vp.run_openspec(d, self.cid)
            self.assertTrue(ok, out)
            ok, out = vp.run_openspec(d, "adlc-sample-backend-13-bad")
            self.assertFalse(ok)
            self.assertIn("scenario", out)
            ok, out = vp.run_openspec(d, "does-not-exist")
            self.assertFalse(ok)

    def test_command_uses_single_pinned_version(self):
        cfg = vp.load_openspec_config()
        cmd = vp.openspec_command(cfg, "abc-123")
        self.assertIn(f"{cfg['package']}@{cfg['version']}", cmd)
        self.assertIn("--no-interactive", cmd)
        self.assertIn("--strict", cmd)
        self.assertIn("abc-123", cmd)


@unittest.skipUnless(HAVE_NPX, "npx not available")
class PublishWithRealCli(tp.Base):
    def test_full_pipeline_with_real_openspec(self):
        ref, cid = self.backend_story()
        tp.write_plan(self.plan, ref, cid, {"adlc-sample-backend": {}})
        res = self.run_publish(ref, cli=True)
        self.assertEqual(res["failures"], [], res["failures"])
        self.assertEqual(res["status"], p.STATUS_REVIEW)

    def test_invalid_plan_is_blocked_by_real_cli(self):
        ref, cid = self.backend_story()
        tp.write_plan(self.plan, ref, cid, {"adlc-sample-backend": {}})
        # structurally plausible for our checks but invalid for OpenSpec: scenario header without body requirement text
        spec = self.plan / "adlc-sample-backend" / "openspec" / "changes" / cid / "specs" / "tasks" / "spec.md"
        spec.write_text("## ADDED Requirements\n### Requirement: Limit\nNo normative keyword here.\n\n"
                        "#### Scenario: s\n- **WHEN** a\n- **THEN** b\n")
        res = self.run_publish(ref, cli=True)
        self.assertEqual(len(res["failures"]), 1)
        self.assertFalse(self.c.has_branch(tp.BACKEND, p.planning_branch(cid)))


if __name__ == "__main__":
    unittest.main()

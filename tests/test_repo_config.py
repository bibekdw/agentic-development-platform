"""Static policy checks over the repository's own configuration and workflows."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import app_scope

WORKFLOWS = ROOT / ".github" / "workflows"
NEW_WORKFLOWS = [WORKFLOWS / "adlc-plan-story.md", WORKFLOWS / "adlc-plan-story.lock.yml", WORKFLOWS / "reconcile.yml"]
SCANNED = [*NEW_WORKFLOWS, ROOT / "templates" / "planning-pr-validate.yml"]


def text(path):
    return path.read_text(encoding="utf-8")


class RepoPolicy(unittest.TestCase):
    def test_single_openspec_version_authority(self):
        cfg = json.loads(text(ROOT / "configs" / "openspec.json"))
        self.assertRegex(cfg["version"], r"^\d+\.\d+\.\d+$")
        offenders = []
        for base in (ROOT / ".github", ROOT / "templates", ROOT / "scripts", ROOT / "docs"):
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in {".yml", ".md", ".py", ".json"} and "openspec" in text(f).lower():
                    if re.search(r"openspec@\d", text(f)) or re.search(r"fission-ai/openspec\s+\d", text(f)):
                        offenders.append(f.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [], "OpenSpec version must only live in configs/openspec.json")

    def test_artifact_snapshot_matches_pin(self):
        pin = json.loads(text(ROOT / "configs" / "openspec.json"))
        snap = json.loads(text(ROOT / "configs" / "openspec-artifacts.json"))
        self.assertEqual(snap["generated_with"], f"{pin['package']}@{pin['version']}",
                         "run scripts/refresh_openspec_artifacts.py after changing the pin")
        self.assertEqual(snap["schema"], pin["schema"])
        self.assertEqual(set(snap["artifact_order"]), {"proposal", "specs", "design", "tasks"})
        for aid in snap["artifact_order"]:
            a = snap["artifacts"][aid]
            self.assertTrue(a["instruction"].strip() and a["template"].strip(), aid)

    def test_no_permanent_planner_token(self):
        for f in SCANNED:
            self.assertNotIn("ADLC_PLANNER_TOKEN", text(f), f.name)

    def test_only_app_credentials_referenced(self):
        for f in SCANNED:
            secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", text(f)))
            allowed = {"ADLC_APP_PRIVATE_KEY", "GITHUB_TOKEN", "GH_AW_GITHUB_TOKEN", "GH_AW_GITHUB_MCP_SERVER_TOKEN",
                       "GH_AW_DEFAULT_OTLP_ENDPOINT", "GH_AW_DEFAULT_OTLP_HEADERS", "COPILOT_GITHUB_TOKEN",
                       "GH_AW_OTEL_ENDPOINT", "GH_AW_OTEL_HEADERS"}
            self.assertLessEqual(secrets - allowed, set(), f.name)

    def test_actions_pinned_to_full_sha(self):
        for f in (WORKFLOWS / "adlc-plan-story.md", WORKFLOWS / "reconcile.yml", ROOT / "templates" / "planning-pr-validate.yml"):
            for m in re.finditer(r"uses:\s*([^\s#]+)", text(f)):
                self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", f"{f.name}: {m.group(1)}")

    def test_no_administration_or_workflow_write_permissions(self):
        for f in SCANNED:
            body = text(f)
            self.assertNotRegex(body, r"permission-(administration|workflows|actions)\s*:\s*write", f.name)
            self.assertNotRegex(body, r"\bworkflows\s*:\s*write", f.name)
            self.assertNotRegex(body, r"\badministration\s*:", f.name)

    def test_planning_is_manual_only(self):
        for f in (WORKFLOWS / "adlc-plan-story.md", WORKFLOWS / "reconcile.yml"):
            body = text(f).split("---")[1] if f.suffix == ".md" else text(f)
            self.assertIn("workflow_dispatch", body)
            for trigger in ("issues:", "issue_comment:", "pull_request:", "push:"):
                self.assertNotRegex(body.split("jobs:")[0], rf"^\s*{trigger}", f.name)

    def test_reconcile_schedule_is_bounded_and_planner_is_not_scheduled(self):
        reconcile = text(WORKFLOWS / "reconcile.yml")
        head = reconcile.split("jobs:")[0]
        self.assertRegex(head, r'(?m)^\s*-\s*cron:\s*"\*/(5|10|15|20|30) \* \* \* \*"')
        self.assertEqual(len(re.findall(r"cron:", head)), 1)
        self.assertIn("--all", reconcile)
        self.assertRegex(reconcile, r"(?m)^permissions:\n  contents: read\n")
        front = text(WORKFLOWS / "adlc-plan-story.md").split("---")[1]
        self.assertNotIn("schedule:", front)
        self.assertNotIn("cron:", front)

    def test_planner_is_bounded_and_serialized(self):
        body = text(WORKFLOWS / "adlc-plan-story.md")
        for key in ("timeout-minutes:", "max-turns:", "max-ai-credits:", "adlc-planning-global"):
            self.assertIn(key, body)

    def test_agent_has_no_github_tools_or_write_safe_outputs(self):
        front = text(WORKFLOWS / "adlc-plan-story.md").split("---")[1]
        self.assertNotIn("github:", front.split("safe-outputs:")[0].split("tools:")[1].split("jobs:")[0])
        for forbidden in ("create-pull-request", "merge-pull-request", "create-issue: true", "add-comment"):
            self.assertNotIn(forbidden, front)

    def test_lock_is_in_sync_marker(self):
        lock = text(WORKFLOWS / "adlc-plan-story.lock.yml")
        self.assertIn("gh-aw", lock)
        self.assertIn("adlc-planning-global", lock)

    def test_app_scope_uses_allowlist(self):
        cfg = json.loads(text(ROOT / "configs" / "repositories.json"))
        scope = app_scope.scope(cfg, "agentic-development-platform")
        self.assertEqual(scope[0], "agentic-development-platform")
        self.assertEqual(scope[1:], cfg["allowed_repositories"])

    def test_template_requires_pinned_platform_sha(self):
        body = text(ROOT / "templates" / "planning-pr-validate.yml")
        self.assertIn("REPLACE_WITH_PLATFORM_MERGE_SHA", body)
        self.assertIn("{40}", body)
        self.assertNotIn("pull_request_target", body)


if __name__ == "__main__":
    unittest.main()

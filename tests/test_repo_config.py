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

    def test_nothing_is_scheduled_and_reconcile_is_manual_only(self):
        reconcile = text(WORKFLOWS / "reconcile.yml")
        head = reconcile.split("jobs:")[0]
        self.assertIn("workflow_dispatch:", head)
        self.assertNotIn("schedule:", head)
        self.assertNotIn("cron:", head)
        self.assertIn("--all", reconcile)
        self.assertRegex(reconcile, r"(?m)^permissions:\n  contents: read\n")
        for f in WORKFLOWS.glob("adlc-*.md"):
            front = text(f).split("---")[1]
            self.assertNotIn("schedule:", front, f.name)
            self.assertNotIn("cron:", front, f.name)

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


class ImplementWorkflowPolicy(unittest.TestCase):
    MD = WORKFLOWS / "adlc-implement.md"
    LOCK = WORKFLOWS / "adlc-implement.lock.yml"

    def front(self):
        return text(self.MD).split("---")[1]

    def test_manual_only_and_dry_run_default(self):
        front = self.front()
        trigger = front.split("permissions:")[0]
        self.assertIn("workflow_dispatch", trigger)
        for t in ("schedule:", "cron:", "issues:", "issue_comment:", "pull_request:", "push:"):
            self.assertNotRegex(trigger, rf"(?m)^\s*{t}")
        self.assertRegex(front, r"dry_run:[\s\S]*?default: true")

    def test_bounded_serialized_and_pinned(self):
        front = self.front()
        for key in ("timeout-minutes:", "max-turns:", "max-ai-credits:", "adlc-planning-global"):
            self.assertIn(key, front)
        for m in re.finditer(r"uses:\s*([^\s#]+)", text(self.MD)):
            self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", m.group(1))

    def test_credentials_and_permissions(self):
        for f in (self.MD, self.LOCK):
            body = text(f)
            self.assertNotRegex(body, r"permission-(administration|workflows|actions)\s*:\s*write")
            self.assertNotRegex(body, r"\badministration\s*:")
        self.assertLessEqual(set(re.findall(r"secrets\.([A-Z0-9_]+)", text(self.MD))), {"ADLC_APP_PRIVATE_KEY"})
        self.assertNotIn("ADLC_PLANNER_TOKEN", text(self.MD))

    def test_agent_has_no_github_tools_or_write_safe_outputs(self):
        front = self.front()
        self.assertNotIn("github:", front.split("safe-outputs:")[0].split("tools:")[1].split("jobs:")[0])
        for forbidden in ("create-pull-request", "merge-pull-request", "create-issue: true", "add-comment"):
            self.assertNotIn(forbidden, front)

    def test_write_token_minted_only_after_isolated_verification(self):
        body = text(self.MD)
        self.assertLess(body.index("Verify in isolated container"), body.index("id: app_write", body.index("submit-implementation")))
        verify = body.split("Verify in isolated container")[1].split("- id: app_write")[0]
        self.assertNotIn("GH_TOKEN", verify)
        self.assertNotIn("secrets.", verify)
        self.assertIn("--cap-drop ALL", verify)

    def test_implementation_profile_is_opt_in_and_excludes_policy_paths(self):
        cfg = json.loads(text(ROOT / "configs" / "repositories.json"))["implementation"]
        self.assertLessEqual(set(cfg["repositories"]), {"adlc-sample-backend", "adlc-sample-worker",
                                                        "adlc-sample-ui", "adlc-sample-deploy"})
        for prefix in cfg["default"]["allowed_path_prefixes"]:
            self.assertFalse(prefix.startswith((".github", "openspec", "AGENTS")), prefix)
            self.assertTrue(prefix.endswith("/"))


class ReviewWorkflowPolicy(unittest.TestCase):
    MD = WORKFLOWS / "adlc-review.md"

    def front(self):
        return text(self.MD).split("---")[1]

    def test_manual_only_dry_run_default_bounded_and_pinned(self):
        front = self.front()
        trigger = front.split("permissions:")[0]
        self.assertIn("workflow_dispatch", trigger)
        for t in ("schedule:", "cron:", "issues:", "issue_comment:", "pull_request:", "push:"):
            self.assertNotRegex(trigger, rf"(?m)^\s*{t}")
        self.assertRegex(front, r"dry_run:[\s\S]*?default: true")
        for key in ("timeout-minutes:", "max-turns:", "max-ai-credits:", "adlc-planning-global"):
            self.assertIn(key, front)
        for m in re.finditer(r"uses:\s*([^\s#]+)", text(self.MD)):
            self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", m.group(1))

    def test_agent_is_read_only_without_network_or_build_tools(self):
        front = self.front()
        agent_part = front.split("safe-outputs:")[0]
        self.assertNotIn("network:", agent_part)  # default network only; no package registries
        self.assertNotRegex(agent_part, r'"(mvn|curl|wget|git|gh|python3?|npm|npx|bash|sh)"')
        self.assertNotIn("github:", agent_part.split("tools:")[1].split("jobs:")[0])
        for forbidden in ("create-pull-request", "merge-pull-request", "create-issue: true", "add-comment",
                          "submit-pull-request-review", "pull-request-review"):
            self.assertNotIn(forbidden, front)

    def test_comment_token_cannot_write_contents(self):
        body = text(self.MD)
        safe = body.split("submit-review:")[1].split("steps:")[1]
        self.assertNotIn("permission-contents: write", safe)
        self.assertNotIn("secrets.", body.replace("secrets.ADLC_APP_PRIVATE_KEY", ""))
        self.assertNotRegex(body, r"permission-(administration|workflows|actions)\s*:\s*write")
        self.assertIn("scripts/mask_token.py", body)


class ReviewChaining(unittest.TestCase):
    IMPL = WORKFLOWS / "adlc-implement.md"
    REVIEW = WORKFLOWS / "adlc-review.md"

    def step(self):
        body = text(self.IMPL)
        return body.split("- name: Start the advisory review")[1].split("- if: always()")[0]

    def test_review_is_started_only_after_a_live_successful_publish(self):
        body = text(self.IMPL)
        self.assertLess(body.index("python3 scripts/implement.py publish"), body.index("Start the advisory review"))
        step = self.step()
        self.assertIn("inputs.dry_run != true", step)
        self.assertIn("inputs.run_review == true", step)
        self.assertNotIn("continue-on-error", step)
        self.assertRegex(text(self.IMPL).split("permissions:")[0], r"run_review:[\s\S]*?default: true")

    def test_dispatch_uses_only_the_workflow_token_never_the_app_token(self):
        step = self.step()
        self.assertIn("GH_TOKEN: ${{ github.token }}", step)
        self.assertNotIn("steps.app", step)
        self.assertNotIn("secrets.", step)
        self.assertIn("gh workflow run adlc-review.lock.yml", step)
        self.assertIn("-f dry_run=false", step)

    def test_actions_write_is_confined_and_not_given_to_the_app(self):
        body = text(self.IMPL)
        self.assertEqual(len(re.findall(r"(?m)^\s+actions:\s*write", body)), 1)
        self.assertNotRegex(body, r"permission-actions")
        self.assertNotRegex(text(self.REVIEW), r"(?m)^\s+actions:\s*write")

    def test_requested_by_is_honoured_only_for_the_actions_bot(self):
        body = text(self.REVIEW)
        self.assertIn("github.actor == 'github-actions[bot]'", body)
        self.assertRegex(body, r"requested_by:[\s\S]*?required: false")


class TokenHygiene(unittest.TestCase):
    def test_runner_mask_command_is_never_printed_by_library_code(self):
        for f in (ROOT / "scripts").glob("*.py"):
            if f.name != "mask_token.py":
                self.assertNotIn("::add-mask::", text(f), f.name)

    def test_workflows_mask_token_separately_and_pack_working_copy(self):
        for f in (WORKFLOWS / "adlc-plan-story.md", WORKFLOWS / "adlc-implement.md", WORKFLOWS / "reconcile.yml"):
            body = text(f)
            self.assertGreaterEqual(body.count("scripts/mask_token.py"), body.count("GH_TOKEN:") - 1, f.name)
        body = text(WORKFLOWS / "adlc-implement.md")
        self.assertIn("post-steps:", body)
        self.assertIn("tree.b64.txt", body)


if __name__ == "__main__":
    unittest.main()

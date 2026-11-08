import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import planning as p

B, U = "bibekdw/adlc-sample-backend", "bibekdw/adlc-sample-ui"
ALLOWED = {B, U, "bibekdw/adlc-sample-worker", "bibekdw/adlc-sample-deploy"}
SHA = "a" * 40


def repo(name, **kw):
    r = {"name": name, "plan_pr": None, "pr_state": "none", "plan_revision": 1,
         "validation": "passed", "contract_refs": []}
    r.update(kw)
    return r


def record(*repos, **kw):
    rec = {"schema_version": 1, "change_id": "adlc-sample-ui-42-task-due-dates",
           "story": "bibekdw/adlc-sample-ui#42", "epic": None, "planning_revision": 1,
           "source_hash": p.source_hash("t", "b"), "open_questions": [], "blocked_by": [],
           "runs": [], "cancelled": False, "shared_contracts": [], "repositories": list(repos)}
    rec.update(kw)
    return rec


def contract_rec(*states):
    return record(
        repo(B, contract_refs=["task-api"], **states[0]),
        repo(U, contract_refs=["task-api"], **states[1]),
        shared_contracts=[{"id": "task-api", "owner": B, "location": "openspec/changes/x/specs/tasks/spec.md"}])


MERGED = {"plan_pr": 1, "pr_state": "merged", "merged_sha": SHA, "approved_revision": 1}
OPEN = {"plan_pr": 2, "pr_state": "open"}


class Refs(unittest.TestCase):
    def test_qualified(self):
        self.assertEqual(p.parse_story_ref("bibekdw/adlc-sample-ui#12"), ("bibekdw", "adlc-sample-ui", 12))
        for bad in ("#12", "12", "a/b#0", "a/b"):
            with self.assertRaises(ValueError):
                p.parse_story_ref(bad)

    def test_same_number_different_repos_separate(self):
        a = p.make_change_id("adlc-sample-backend", 12, "Due dates")
        b = p.make_change_id("adlc-sample-ui", 12, "Due dates")
        self.assertNotEqual(a, b)
        self.assertNotEqual(p.planning_branch(a), p.planning_branch(b))

    def test_record_roundtrip(self):
        r = record(repo(U))
        self.assertEqual(p.parse_record_block("x\n" + p.render_record_block(r)), r)


class Validation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(p.validate_record(record(repo(U)), ALLOWED), [])

    def test_unqualified_story_and_mismatch(self):
        self.assertTrue(p.validate_record(record(repo(U), story="42")))
        self.assertTrue(p.validate_record(record(repo(U), story="bibekdw/adlc-sample-ui#43")))

    def test_not_allowlisted_and_empty(self):
        self.assertTrue(p.validate_record(record(repo("evil/repo")), ALLOWED))
        self.assertTrue(p.validate_record(record()))

    def test_merged_requires_sha(self):
        r = record(repo(U, plan_pr=1, pr_state="merged", approved_revision=1))
        self.assertTrue(p.validate_record(r))

    def test_contract_undefined_and_duplicate(self):
        r = record(repo(U, contract_refs=["nope"]))
        self.assertTrue(p.check_contracts(r))
        r = contract_rec({}, {})
        r["shared_contracts"].append(dict(r["shared_contracts"][0]))
        self.assertTrue(p.check_contracts(r))

    def test_contract_owner_must_be_affected(self):
        r = contract_rec({}, {})
        r["shared_contracts"][0]["owner"] = "bibekdw/adlc-sample-worker"
        self.assertTrue(p.check_contracts(r))


class Paths(unittest.TestCase):
    cid = "adlc-sample-ui-42-task-due-dates"

    def test_allowed(self):
        self.assertEqual(p.check_changed_paths(self.cid, [f"openspec/changes/{self.cid}/proposal.md"]), [])

    def test_rejected(self):
        for bad in ("src/app.py", ".github/workflows/x.yml", "openspec/specs/a/spec.md",
                    f"openspec/changes/{self.cid}/../../../x", "openspec/changes/other/proposal.md"):
            self.assertTrue(p.check_changed_paths(self.cid, [bad]), bad)


class Readiness(unittest.TestCase):
    def test_one_repo_ready_after_merge(self):
        self.assertEqual(p.compute_status(record(repo(B, **OPEN)))[0], p.STATUS_REVIEW)
        self.assertEqual(p.compute_status(record(repo(B, **MERGED)))[0], p.STATUS_READY)

    def test_not_created_is_planning(self):
        self.assertEqual(p.compute_status(record(repo(B)))[0], p.STATUS_PLANNING)

    def test_one_of_two_merged_not_ready(self):
        r = contract_rec(MERGED, OPEN)
        self.assertEqual(p.compute_status(r)[0], p.STATUS_REVIEW)
        r = contract_rec(MERGED, {**MERGED, "plan_pr": 3})
        self.assertEqual(p.compute_status(r)[0], p.STATUS_READY)

    def test_closed_unmerged_blocks(self):
        r = contract_rec(MERGED, {"plan_pr": 3, "pr_state": "closed"})
        self.assertNotEqual(p.compute_status(r)[0], p.STATUS_READY)

    def test_clarification_blocked_cancelled_failed(self):
        base = record(repo(B, **MERGED))
        self.assertEqual(p.compute_status({**base, "open_questions": ["q?"]})[0], p.STATUS_CLARIFY)
        self.assertEqual(p.compute_status({**base, "blocked_by": [{"story": "a/b#1", "ready": False}]})[0], p.STATUS_BLOCKED)
        self.assertEqual(p.compute_status({**base, "cancelled": True})[0], p.STATUS_CANCELLED)
        failed = record(repo(B, **MERGED, ) | {"validation": "failed"})
        self.assertEqual(p.compute_status(failed)[0], p.STATUS_FAILED)

    def test_new_revision_invalidates_approval(self):
        r = p.bump_revision(record(repo(B, **MERGED)), p.source_hash("t", "changed"))
        self.assertEqual(r["planning_revision"], 2)
        self.assertNotEqual(p.compute_status(r)[0], p.STATUS_READY)

    def test_same_hash_no_bump(self):
        r = record(repo(B, **MERGED))
        self.assertEqual(p.bump_revision(r, r["source_hash"]), r)

    def test_apply_pr_states_records_approval(self):
        r = record(repo(B, **OPEN))
        r2 = p.apply_pr_states(r, {B: {"number": 2, "state": "closed", "merged": True, "merge_commit_sha": SHA}})
        self.assertEqual(r2["repositories"][0]["merged_sha"], SHA)
        self.assertEqual(p.compute_status(r2)[0], p.STATUS_READY)

    def test_unrelated_pr_does_not_change_readiness(self):
        r = record(repo(B, **OPEN))
        r2 = p.apply_pr_states(r, {"bibekdw/adlc-sample-worker": {"number": 9, "state": "closed", "merged": True,
                                                                  "merge_commit_sha": SHA}})
        self.assertEqual(p.compute_status(r2)[0], p.STATUS_REVIEW)


class Retry(unittest.TestCase):
    def test_no_duplicate_prs(self):
        r = contract_rec(OPEN, {})
        acts = p.plan_actions(r, {})
        self.assertEqual([a["action"] for a in acts], ["reuse", "create"])
        r2 = p.plan_actions(r, {U: 7})
        self.assertEqual([a["action"] for a in r2], ["reuse", "reuse"])

    def test_merged_skipped_and_stale_updated(self):
        r = record(repo(B, **MERGED), planning_revision=1)
        self.assertEqual(p.plan_actions(r, {})[0]["action"], "skip-approved")
        r = record(repo(B, **OPEN, ) | {"plan_revision": 1}, planning_revision=2)
        self.assertEqual(p.plan_actions(r, {})[0]["action"], "update-existing")


class ChangeFolder(unittest.TestCase):
    cid = "adlc-sample-backend-12-title-length"
    story = "bibekdw/adlc-sample-backend#12"

    def build(self, root, proposal=None, scenario=True):
        base = Path(root) / "openspec" / "changes" / self.cid
        (base / "specs" / "tasks").mkdir(parents=True)
        (base / "proposal.md").write_text(proposal or f"{self.cid} {self.story} contract task-api")
        (base / "design.md").write_text("design")
        (base / "tasks.md").write_text("- [ ] t")
        sc = "#### Scenario: 201 chars rejected\n" if scenario else ""
        (base / "specs" / "tasks" / "spec.md").write_text(f"## ADDED Requirements\n### Requirement: x\n{sc}")

    def test_valid(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d)
            self.assertEqual(p.validate_change_folder(d, self.cid, self.story, ["task-api"]), [])

    def test_missing_story_scenario_contract(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d, proposal="nothing", scenario=False)
            errs = p.validate_change_folder(d, self.cid, self.story, ["task-api"])
            self.assertGreaterEqual(len(errs), 4)

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(p.validate_change_folder(d, self.cid, self.story))


if __name__ == "__main__":
    unittest.main()

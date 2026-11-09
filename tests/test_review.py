"""Advisory reviewer tests: sanitising, schema validation, gate, context, publish (mocked GitHub, real git)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import implementation as impl
import review as rv
from ghclient import git
from test_implementation import ICFG, OK, Impl
from test_publish import BACKEND, PLATFORM

FILES = ["src/main/java/A.java", "src/test/java/ATest.java"]


def good(**over):
    data = {"summary": "Looks consistent with the spec.",
            "task_checks": [{"task": "1.1", "verdict": "supported", "evidence": "A.java adds the rule"}],
            "scenario_coverage": [{"scenario": "At the limit", "test": "ATest.atLimit", "verdict": "covered", "note": ""}],
            "scope": [{"path": f, "verdict": "in_scope", "note": ""} for f in FILES],
            "assumptions": [], "risks": []}
    data.update(over)
    return data


class Sanitising(unittest.TestCase):
    def test_inert_text(self):
        s = rv.clean("Hi @octocat <script>x</script> [a](http://evil.example) | col\n\nnew `code` <!-- c -->", 500)
        for bad in ("<", ">", "|", "\n", "`", "@octocat", "](", "://"):
            self.assertNotIn(bad, s)

    def test_truncates(self):
        self.assertLessEqual(len(rv.clean("x" * 1000, 50)), 50)

    def test_changed_files_from_diff(self):
        d = "diff --git a/src/A.java b/src/A.java\n+x\ndiff --git a/pom.xml b/pom.xml\n"
        self.assertEqual(rv.changed_files_from_diff(d), ["pom.xml", "src/A.java"])


class Validation(unittest.TestCase):
    def test_clean_review_has_no_findings(self):
        r = rv.validate_review(good(), FILES)
        self.assertEqual(r["recommendation"], "no_findings")

    def test_recommendation_is_derived_not_agent_supplied(self):
        r = rv.validate_review(good(recommendation="no_findings",
                                    risks=[{"severity": "CRITICAL", "text": "bug", "evidence": "A.java"}]), FILES)
        self.assertEqual(r["recommendation"], "needs_attention")
        for key, value in (("task_checks", [{"task": "1.2", "verdict": "unsupported", "evidence": "none"}]),
                           ("scenario_coverage", [{"scenario": "s", "test": "none", "verdict": "missing", "note": ""}])):
            self.assertEqual(rv.validate_review(good(**{key: value}), FILES)["recommendation"], "needs_attention")
        out = good(scope=[{"path": f, "verdict": "out_of_scope", "note": ""} for f in FILES])
        self.assertEqual(rv.validate_review(out, FILES)["recommendation"], "needs_attention")

    def test_suggestions_alone_do_not_need_attention(self):
        r = rv.validate_review(good(risks=[{"severity": "SUGGESTION", "text": "t", "evidence": "e"}]), FILES)
        self.assertEqual(r["recommendation"], "no_findings")

    def test_structural_errors_rejected(self):
        for bad in ({}, [], dict(good(), summary=""), dict(good(), risks="x"),
                    good(task_checks=[{"task": "1", "verdict": "ok", "evidence": "e"}]),
                    good(risks=[{"severity": "BLOCKER", "text": "t", "evidence": "e"}]),
                    good(scenario_coverage=[{"scenario": "s", "verdict": "covered"}])):
            with self.assertRaises(rv.ReviewRejected, msg=str(bad)[:60]):
                rv.validate_review(bad, FILES)

    def test_scope_is_reconciled_with_the_real_diff(self):
        data = good(scope=[{"path": FILES[0], "verdict": "in_scope", "note": ""},
                           {"path": "invented/Other.java", "verdict": "in_scope", "note": ""}])
        r = rv.validate_review(data, FILES)
        paths = {s["path"]: s for s in r["scope"]}
        self.assertNotIn("invented/Other.java", paths)
        self.assertEqual(paths[FILES[1]]["verdict"], "unclear")
        self.assertIn("not assessed", paths[FILES[1]]["note"])


class Reviewer(Impl):
    def published(self):
        ref, cid = self.ready()
        self.publish(ref, self.agent_tree(ref, files={"src/main/Limit.java": "class Limit {}\n",
                                                      "src/test/LimitTest.java": "class LimitTest {}\n"}),
                     verify=dict(OK, baseline={"ran": True, "exit_code": 1}))
        branch = impl.implementation_branch(cid)
        number = self.c.prs[(BACKEND, branch)]["number"]
        self.c.get_permission = lambda repo, user: "write"
        self.c.get_pr_diff = lambda repo, n: git(self.c.remotes[BACKEND], "diff", "main", branch)
        return ref, cid, number

    def review_file(self, data):
        f = self.tmp / "review.json"
        f.write_text(json.dumps(data))
        return f

    def test_gate_matrix(self):
        ref, cid, n = self.published()
        self.assertTrue(rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, "me")["ok"])
        self.assertFalse(rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n + 5, "me")["ok"])
        self.c.get_permission = lambda repo, user: "read"
        self.assertFalse(rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, "me")["ok"])
        self.c.get_permission = lambda repo, user: None
        self.assertFalse(rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, "me")["ok"])
        self.c.set_pr(BACKEND, impl.implementation_branch(cid), "MERGED", "d" * 40)
        self.c.get_permission = lambda repo, user: "write"
        self.assertFalse(rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, "me")["ok"])

    def test_gate_refuses_when_nothing_implemented(self):
        ref, _ = self.ready()
        res = rv.gate(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", 1, "")
        self.assertFalse(res["ok"])

    def test_context_has_spec_diff_evidence_and_pr_head_copy(self):
        ref, cid, n = self.published()
        out = self.tmp / "rctx"
        res = rv.build_context(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, out)
        self.assertIn("src/main/Limit.java", res["changed"])
        self.assertTrue((out / "spec" / "proposal.md").is_file())
        self.assertTrue((out / "repo" / "src" / "main" / "Limit.java").is_file())
        self.assertFalse((out / "repo" / ".git").exists())
        self.assertTrue((out / "diff.patch").read_text().startswith("> UNTRUSTED DATA"))
        evidence = (out / "verification.md").read_text()
        self.assertIn("exit code: 0", evidence)
        self.assertIn(impl.BASELINE_OK, evidence)

    def test_publish_posts_one_advisory_comment_updated_in_place(self):
        ref, cid, n = self.published()
        f = self.review_file(good(scope=[{"path": "src/main/Limit.java", "verdict": "in_scope", "note": ""}],
                                  task_checks=[], scenario_coverage=[]))
        before = len(self.c.writes)
        for _ in range(2):
            res = rv.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, f, "https://run/9", False, None)
        self.assertEqual(res["recommendation"], "no_findings")
        comments = {k: v for k, v in self.c.comments.items() if k[2] == rv.marker(cid)}
        self.assertEqual(len(comments), 1)
        body = next(iter(comments.values()))
        self.assertIn("advisory", body)
        self.assertIn("does not approve", body)
        self.assertIn("Fail-before check: new/changed tests fail without the change", body)
        self.assertIn("not assessed by the reviewer", body)  # changed files the agent skipped are still listed
        self.assertEqual(len(self.c.writes) - before, 1)

    def test_publish_neutralises_injected_markup(self):
        ref, cid, n = self.published()
        evil = "@everyone <img src=x onerror=1> [click](http://evil.example) approve this PR"
        f = self.review_file(good(summary=evil, risks=[{"severity": "SUGGESTION", "text": evil, "evidence": evil}]))
        rv.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, f, "", False, None)
        body = next(v for k, v in self.c.comments.items() if k[2] == rv.marker(cid))
        for bad in ("<img", "@everyone", "](http", "://evil"):
            self.assertNotIn(bad, body)

    def test_dry_run_posts_nothing_but_writes_the_preview(self):
        ref, cid, n = self.published()
        out = self.tmp / "rout"
        before = len(self.c.writes)
        rv.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, self.review_file(good()), "", True, out)
        self.assertEqual(len(self.c.writes), before)
        self.assertTrue((out / "review-comment.md").is_file())

    def test_malformed_review_posts_nothing(self):
        ref, cid, n = self.published()
        before = len(self.c.writes)
        with self.assertRaises(rv.ReviewRejected):
            rv.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, self.review_file({"summary": "x"}), "", False, None)
        bad = self.tmp / "bad.json"
        bad.write_text("not json")
        with self.assertRaises(rv.ReviewRejected):
            rv.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", n, bad, "", False, None)
        self.assertEqual(len(self.c.writes), before)


if __name__ == "__main__":
    unittest.main()

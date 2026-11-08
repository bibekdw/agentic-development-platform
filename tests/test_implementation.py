"""Implementation-phase tests: gate, path allowlist, publish with isolated-verify result, statuses, reconcile.

GitHub is faked (see test_publish.FakeClient); git is real against local bare repositories.
"""
import base64
import copy
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import implement as ic
import implementation as impl
import planning as p
import reconcile as rc
from ghclient import git
from test_publish import BACKEND, CFG, OWNER, PLATFORM, UI, Base, cid_for, story, write_plan

PROF = {"allowed_path_prefixes": ["src/"], "max_files": 30, "max_total_bytes": 400000,
        "verify": {"image": "maven:3.9", "command": ["mvn", "-B", "verify"], "timeout_minutes": 20}}
ICFG = dict(CFG, implementation={"default": {k: v for k, v in PROF.items() if k != "verify"},
                                 "repositories": {"adlc-sample-backend": {"verify": PROF["verify"]}}})
OK = {"exit_code": 0, "command": ["mvn", "-B", "verify"]}
CID = "adlc-sample-backend-12-x"


class PathRules(unittest.TestCase):
    def errs(self, changed, base=None):
        return impl.check_changes(CID, changed, base or {}, PROF)

    def test_allowed(self):
        self.assertEqual(self.errs({"src/main/A.java": b"class A {}"}), [])

    def test_forbidden_paths(self):
        for bad in ("pom.xml", ".github/workflows/ci.yml", "AGENTS.md", "openspec/specs/a/spec.md",
                    "../x", "/etc/passwd", "src/../pom.xml", ".git/config", "openspec/changes/other/tasks.md"):
            self.assertTrue(self.errs({bad: b"x"}), bad)

    def test_tasks_only_checkbox_ticks(self):
        t = impl.tasks_path(CID)
        old = b"## 1.\n- [ ] 1.1 a\n- [ ] 1.2 b\n"
        self.assertEqual(self.errs({t: b"## 1.\n- [x] 1.1 a\n- [ ] 1.2 b\n"}, {t: old}), [])
        self.assertTrue(self.errs({t: b"## 1.\n- [x] 1.1 a\n- [ ] 1.2 changed\n"}, {t: old}))
        self.assertTrue(self.errs({t: None}, {t: old}))

    def test_limits_binary_and_empty(self):
        self.assertTrue(self.errs({}))
        self.assertTrue(self.errs({f"src/{i}.java": b"x" for i in range(31)}))
        self.assertTrue(self.errs({"src/a": b"x" * 400001}))
        self.assertTrue(self.errs({"src/a.bin": b"\x00\x01"}))
        self.assertTrue(self.errs({"src/a": b"\xff\xfe"}))

    def test_profile_requires_opt_in(self):
        self.assertIsNone(impl.profile(ICFG, "adlc-sample-ui"))
        self.assertEqual(impl.profile(ICFG, "adlc-sample-backend")["max_files"], 30)


class PackRules(unittest.TestCase):
    """The agent artifact only keeps text file types, so the working copy travels as base64 tar.gz."""

    def pack(self, build):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            build(tf)
        f = Path(tempfile.mkdtemp()) / "tree.b64.txt"
        f.write_text(base64.b64encode(buf.getvalue()).decode())
        return f

    @staticmethod
    def add(tf, name, data=b"x", typ=tarfile.REGTYPE, link=""):
        info = tarfile.TarInfo(name)
        info.type, info.linkname = typ, link
        info.size = len(data) if typ == tarfile.REGTYPE else 0
        tf.addfile(info, io.BytesIO(data) if typ == tarfile.REGTYPE else None)

    def test_roundtrip_includes_hidden_and_non_text_types(self):
        def build(tf):
            self.add(tf, "./pom.xml", b"<p/>")
            self.add(tf, "./src/main/A.java", b"class A {}")
            self.add(tf, "./.adlc-base-sha", b"a" * 40)
            self.add(tf, "./Dockerfile", b"FROM x")
        out = Path(tempfile.mkdtemp())
        self.assertEqual(impl.unpack_tree(self.pack(build), out), 4)
        self.assertEqual((out / "src/main/A.java").read_bytes(), b"class A {}")
        self.assertTrue((out / ".adlc-base-sha").is_file())

    def test_unsafe_members_rejected(self):
        cases = {"symlink": lambda tf: self.add(tf, "src/l", typ=tarfile.SYMTYPE, link="/etc/passwd"),
                 "hardlink": lambda tf: self.add(tf, "src/h", typ=tarfile.LNKTYPE, link="pom.xml"),
                 "traversal": lambda tf: self.add(tf, "../evil.txt"),
                 "absolute": lambda tf: self.add(tf, "/tmp/evil.txt")}
        for label, build in cases.items():
            out = Path(tempfile.mkdtemp())
            with self.assertRaises(impl.ImplRejected, msg=label):
                impl.unpack_tree(self.pack(build), out)
            self.assertEqual(list(out.iterdir()), [], label)

    def test_invalid_archives_rejected(self):
        bad = Path(tempfile.mkdtemp()) / "t.txt"
        bad.write_text("not base64 !!!")
        with self.assertRaises(impl.ImplRejected):
            impl.unpack_tree(bad, Path(tempfile.mkdtemp()))
        bad.write_text(base64.b64encode(b"not a tarball").decode())
        with self.assertRaises(impl.ImplRejected):
            impl.unpack_tree(bad, Path(tempfile.mkdtemp()))
        with self.assertRaises(impl.ImplRejected):
            impl.unpack_tree(bad.parent / "missing.txt", Path(tempfile.mkdtemp()))


class Impl(Base):
    def ready(self, cfg=ICFG):
        ref, cid = self.backend_story()
        write_plan(self.plan, ref, cid, {"adlc-sample-backend": {}})
        self.run_publish(ref)
        branch = p.planning_branch(cid)
        clone = self.tmp / "merge"
        git(".", "clone", "-q", str(self.c.remotes[BACKEND]), str(clone))
        git(clone, "merge", "-q", "--ff-only", f"origin/{branch}")
        git(clone, "push", "-q", "origin", "main")
        self.c.set_pr(BACKEND, branch, "MERGED", git(clone, "rev-parse", "HEAD"))
        res = rc.reconcile(self.c, cfg, PLATFORM, cid)
        self.assertEqual(res["status"], p.STATUS_READY)
        return ref, cid

    def agent_tree(self, ref, files=None, tick=True):
        out, work = self.tmp / "ctx", self.tmp / "impl"
        ic.build_context(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", out, work)
        tree = work / "adlc-sample-backend"
        for rel, text in (files if files is not None else {"src/main/Limit.java": "class Limit {}\n"}).items():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(text)
        if tick:
            t = next(tree.glob("openspec/changes/*/tasks.md"))
            t.write_text(t.read_text().replace("- [ ]", "- [x]"))
        return tree

    def publish(self, ref, tree, verify=OK, dry_run=False, out=None):
        work = self.tmp / f"pw{len(list(self.tmp.glob('pw*')))}"
        work.mkdir()
        return ic.publish(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", tree, verify, "https://run/2",
                          dry_run, out, work)


class Gate(Impl):
    def gate(self, ref, actor="bibekdw", cfg=ICFG, perm="admin"):
        self.c.get_permission = lambda repo, user: perm
        return ic.gate(self.c, cfg, PLATFORM, ref, "adlc-sample-backend", actor)

    def test_ready_passes(self):
        ref, cid = self.ready()
        res = self.gate(ref)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["change_id"], cid)

    def test_not_ready_refused(self):
        ref, cid = self.backend_story()
        write_plan(self.plan, ref, cid, {"adlc-sample-backend": {}})
        self.run_publish(ref)
        self.assertFalse(self.gate(ref)["ok"])

    def test_unauthorized_refused(self):
        ref, _ = self.ready()
        self.assertFalse(self.gate(ref, perm="read")["ok"])
        self.assertFalse(self.gate(ref, perm=None)["ok"])

    def test_repo_without_profile_refused(self):
        ref, _ = self.ready()
        self.c.get_permission = lambda repo, user: "admin"
        problems = impl.evaluate_gate(p.parse_record_block(self.c.tracking[0]["body"]), UI, ICFG, "admin", None)
        self.assertTrue(any("no implementation profile" in x for x in problems))
        self.assertTrue(any("not an affected repository" in x for x in problems))

    def test_unknown_story_refused(self):
        with self.assertRaises(impl.ImplRejected):
            self.gate("bibekdw/adlc-sample-backend#99")


class Publish(Impl):
    def test_success_opens_draft_pr_and_updates_record(self):
        ref, cid = self.ready()
        tree = self.agent_tree(ref)
        res = self.publish(ref, tree)
        self.assertEqual(res["failures"], [])
        self.assertEqual(res["status"], p.STATUS_IMPL_REVIEW)
        branch = impl.implementation_branch(cid)
        added = set(self.c.remote_files(BACKEND, branch)) - set(self.c.remote_files(BACKEND, "main"))
        self.assertEqual(added, {"src/main/Limit.java"})
        self.assertNotIn(".adlc-base-sha", self.c.remote_files(BACKEND, branch))
        self.assertIn("- [x]", self.c.remote_blob(BACKEND, branch, impl.tasks_path(cid)))
        rec = p.parse_record_block(self.c.tracking[0]["body"])
        entry = rec["implementation"][BACKEND]
        self.assertEqual((entry["pr_state"], entry["verify"]["exit_code"]), ("open", 0))
        body = self.c.prs[(BACKEND, branch)]["body"]
        self.assertIn("Exit code: 0", body)
        self.assertIn("src/main/Limit.java", body)

    def test_failed_verify_opens_no_pr(self):
        ref, cid = self.ready()
        tree = self.agent_tree(ref)
        before = len(self.c.prs)
        res = self.publish(ref, tree, verify={"exit_code": 1, "command": ["mvn"]})
        self.assertEqual(len(res["failures"]), 1)
        self.assertEqual(len(self.c.prs), before)
        self.assertFalse(self.c.has_branch(BACKEND, impl.implementation_branch(cid)))
        rec = p.parse_record_block(self.c.tracking[0]["body"])
        self.assertIn("verification", rec["last_failure"]["message"])
        self.assertNotIn("implementation", rec)
        self.assertEqual(res["status"], p.STATUS_READY)

    def test_missing_verify_result_is_failure(self):
        ref, _ = self.ready()
        res = self.publish(ref, self.agent_tree(ref), verify={})
        self.assertEqual(len(res["failures"]), 1)

    def test_forbidden_changes_rejected(self):
        ref, cid = self.ready()
        for bad in ({"pom.xml": "<x/>"}, {".github/workflows/x.yml": "x"}, {"openspec/specs/a.md": "x"}):
            with self.assertRaises(impl.ImplRejected, msg=str(bad)):
                self.publish(ref, self.agent_tree(ref, files=bad))
        self.assertFalse(self.c.has_branch(BACKEND, impl.implementation_branch(cid)))

    def test_no_changes_rejected(self):
        ref, _ = self.ready()
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, self.agent_tree(ref, files={}, tick=False))

    def test_tasks_rewrite_rejected(self):
        ref, _ = self.ready()
        tree = self.agent_tree(ref, tick=False)
        t = next(tree.glob("openspec/changes/*/tasks.md"))
        t.write_text(t.read_text() + "- [ ] 9.9 extra\n")
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, tree)

    def test_symlink_rejected(self):
        ref, _ = self.ready()
        tree = self.agent_tree(ref)
        try:
            (tree / "src" / "link").symlink_to(tree / "README.md")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, tree)

    def test_base_sha_must_be_on_default_branch_and_valid(self):
        ref, _ = self.ready()
        tree = self.agent_tree(ref)
        (tree / impl.BASE_FILE).write_text("not-a-sha")
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, tree)
        (tree / impl.BASE_FILE).write_text("c" * 40)
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, tree)

    def test_diff_is_relative_to_recorded_base_when_main_moves(self):
        ref, cid = self.ready()
        tree = self.agent_tree(ref)
        clone = self.tmp / "mv"
        git(".", "clone", "-q", str(self.c.remotes[BACKEND]), str(clone))
        (clone / "src").mkdir(exist_ok=True)
        (clone / "src" / "Other.java").write_text("class Other {}\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "other work")
        git(clone, "push", "-q", "origin", "main")
        res = self.publish(ref, tree)
        self.assertEqual(res["changed"], sorted(["src/main/Limit.java", impl.tasks_path(cid)]))

    def test_second_attempt_refused_while_pr_open(self):
        ref, _ = self.ready()
        self.publish(ref, self.agent_tree(ref))
        with self.assertRaises(impl.ImplRejected):
            self.publish(ref, self.agent_tree(ref))

    def test_dry_run_writes_nothing(self):
        ref, cid = self.ready()
        tree = self.agent_tree(ref)
        writes = len(self.c.writes)
        out = self.tmp / "dry"
        res = self.publish(ref, tree, dry_run=True, out=out)
        self.assertEqual(len(self.c.writes), writes)
        self.assertFalse(self.c.has_branch(BACKEND, impl.implementation_branch(cid)))
        self.assertTrue((out / "pr.md").is_file())
        self.assertEqual(res["failures"], [])

    def test_prepare_writes_verification_tree(self):
        ref, cid = self.ready()
        tree = self.agent_tree(ref)
        out = self.tmp / "prep"
        res = ic.prepare(self.c, ICFG, PLATFORM, ref, "adlc-sample-backend", tree, out)
        self.assertIn("src/main/Limit.java", res["changed"])
        self.assertTrue((out / "patched" / "src" / "main" / "Limit.java").is_file())
        self.assertFalse((out / "patched" / ".git").exists())
        v = json.loads((out / "verify.json").read_text())
        self.assertEqual(v["command"], PROF["verify"]["command"])


class Lifecycle(Impl):
    def test_status_transitions_and_reconcile_skips(self):
        ref, cid = self.ready()
        # a Ready change with no implementation started is skipped by the scheduled run
        out = rc.reconcile_all(self.c, ICFG, PLATFORM)
        self.assertTrue(out["results"][0].get("skipped"))
        self.publish(ref, self.agent_tree(ref))
        out = rc.reconcile_all(self.c, ICFG, PLATFORM)
        self.assertEqual(out["results"][0]["status"], p.STATUS_IMPL_REVIEW)
        self.assertNotIn("skipped", out["results"][0])
        self.c.set_pr(BACKEND, impl.implementation_branch(cid), "MERGED", "d" * 40)
        out = rc.reconcile_all(self.c, ICFG, PLATFORM)
        self.assertEqual(out["results"][0]["status"], p.STATUS_IMPLEMENTED)
        rec = p.parse_record_block(self.c.tracking[0]["body"])
        self.assertEqual(rec["implementation"][BACKEND]["merged_sha"], "d" * 40)
        self.assertIn("Implemented", next(iter(self.c.comments.values())))
        out = rc.reconcile_all(self.c, ICFG, PLATFORM)
        self.assertTrue(out["results"][0].get("skipped"))

    def test_closed_unmerged_impl_pr_returns_to_ready(self):
        ref, cid = self.ready()
        self.publish(ref, self.agent_tree(ref))
        self.c.set_pr(BACKEND, impl.implementation_branch(cid), "CLOSED")
        res = rc.reconcile(self.c, ICFG, PLATFORM, cid)
        self.assertEqual(res["status"], p.STATUS_READY)

    def test_status_function_multi_repo(self):
        rec = {"repositories": [{"name": "a/x"}, {"name": "a/y"}], "implementation": {"a/x": {"pr_state": "merged"}}}
        self.assertEqual(p.implementation_status(rec)[0], p.STATUS_READY)
        rec["implementation"]["a/y"] = {"pr_state": "open", "pr": 3}
        self.assertEqual(p.implementation_status(rec)[0], p.STATUS_IMPL_REVIEW)
        rec["implementation"]["a/y"]["pr_state"] = "merged"
        self.assertEqual(p.implementation_status(rec)[0], p.STATUS_IMPLEMENTED)


if __name__ == "__main__":
    unittest.main()

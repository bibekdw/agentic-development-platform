import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preflight as pf

CFG = {"owner": "bibekdw", "allowed_repositories": ["adlc-sample-backend"],
       "authorized_permissions": ["admin", "maintain", "write"]}
REF = "bibekdw/adlc-sample-backend#12"
GOOD = {"state": "open", "body": "## Problem\nx\n## Acceptance criteria\n- [ ] 201 chars rejected\n- 200 accepted\n## Notes\n- misc item"}


class Preflight(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(pf.evaluate(REF, GOOD, "write", CFG), (True, [], []))

    def test_not_allowlisted(self):
        ok, probs, _ = pf.evaluate("bibekdw/other#1", GOOD, "write", CFG)
        self.assertFalse(ok)
        self.assertTrue(probs)

    def test_unauthorized(self):
        self.assertFalse(pf.evaluate(REF, GOOD, "read", CFG)[0])
        self.assertFalse(pf.evaluate(REF, GOOD, None, CFG)[0])

    def test_closed_missing_or_pr(self):
        self.assertFalse(pf.evaluate(REF, {**GOOD, "state": "closed"}, "write", CFG)[0])
        self.assertFalse(pf.evaluate(REF, None, "write", CFG)[0])
        self.assertFalse(pf.evaluate(REF, {**GOOD, "pull_request": {}}, "write", CFG)[0])

    def test_missing_acceptance_criteria_asks_questions(self):
        ok, probs, qs = pf.evaluate(REF, {"state": "open", "body": "just do it"}, "write", CFG)
        self.assertFalse(ok)
        self.assertEqual(probs, [])
        self.assertTrue(qs)

    def test_criteria_only_from_section(self):
        self.assertEqual(len(pf.acceptance_criteria(GOOD["body"])), 2)

    def test_unqualified_ref(self):
        self.assertFalse(pf.evaluate("12", GOOD, "write", CFG)[0])


if __name__ == "__main__":
    unittest.main()

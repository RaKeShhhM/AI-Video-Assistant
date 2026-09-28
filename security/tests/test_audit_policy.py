import copy
import importlib.util
import json
from datetime import date
from pathlib import Path
import unittest

SECURITY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_python", SECURITY / "audit_python.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((SECURITY / "python-audit-policy.json").read_text())
        self.policy["exceptions"][0]["approved"] = True  # simulate explicit approval
        self.report = {"dependencies": [{"name": "chromadb", "version": "1.5.9", "vulns": [
            {"id": key, "fix_versions": []} for key in self.policy["exceptions"][0]["advisory_ids"]]}]}

    def check(self, code=1, today=date(2026, 9, 28)):
        return audit.evaluate(self.report, self.policy, code, today)

    def test_pending_approval_blocks_and_explicit_approval_accepts_only_reviewed_findings(self):
        self.policy["exceptions"][0]["approved"] = False
        self.assertEqual(len(self.check()[1]), 4)
        self.policy["exceptions"][0]["approved"] = True
        self.assertEqual(len(self.check()[0]), 4)
        self.assertEqual(self.check()[1], [])

    def test_new_ids_other_packages_and_available_fixes_are_blocked(self):
        self.report["dependencies"][0]["vulns"].append({"id": "NEW-ISSUE", "fix_versions": []})
        self.report["dependencies"].append({"name": "other", "version": "1.5.9", "vulns": [{"id": "PYSEC-2026-311", "fix_versions": []}]})
        self.report["dependencies"][0]["vulns"][0]["fix_versions"] = ["1.5.10"]
        self.assertEqual(len(self.check()[1]), 3)

    def test_expiry_is_utc_date_boundary_and_cannot_be_extended_beyond_30_days(self):
        self.assertEqual(self.check(today=date(2026, 10, 27))[1], [])
        with self.assertRaises(ValueError): self.check(today=date(2026, 10, 28))
        self.policy["exceptions"][0]["expires_on"] = "2027-01-01"
        with self.assertRaises(ValueError): self.check()

    def test_version_change_missing_package_or_changed_scope_fails(self):
        self.report["dependencies"][0]["version"] = "1.5.10"
        with self.assertRaises(ValueError): self.check()
        self.report["dependencies"][0]["version"] = "1.5.9"
        self.policy["exceptions"][0]["advisory_ids"].append("NEW-ISSUE")
        with self.assertRaises(ValueError): self.check()

    def test_collection_failures_and_inconsistent_exit_status_fail_closed(self):
        for code in (0, 2, -1):
            with self.subTest(code=code), self.assertRaises(ValueError): self.check(code)
        for report in ({}, {"dependencies": []}, {"dependencies": [{"name": "x", "skip_reason": "not found"}]}):
            with self.assertRaises(ValueError): audit.evaluate(report, self.policy, 1, date(2026, 9, 28))
        self.report["dependencies"][0]["vulns"] = []
        with self.assertRaises(ValueError): self.check(1)
        self.assertEqual(self.check(0), ([], []))

    def test_duplicate_advisory_entries_are_deduplicated_without_hiding_new_findings(self):
        self.report["dependencies"][0]["vulns"] *= 2
        self.assertEqual(len(self.check()[0]), 4)
        bad = copy.deepcopy(self.report)
        del bad["dependencies"][0]["vulns"][0]["fix_versions"]
        with self.assertRaises(ValueError): audit.evaluate(bad, self.policy, 1, date(2026, 9, 28))


if __name__ == "__main__":
    unittest.main()

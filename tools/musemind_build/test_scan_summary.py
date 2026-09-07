"""Local synthetic tests, never invoking Docker or reading real scan reports."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("scan_summary", Path(__file__).with_name("scan_summary.py"))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
IMAGE = "sha256:" + "a" * 64


def report(secret=False):
    return {
        "SchemaVersion": 2,
        "ArtifactType": "container_image",
        "Metadata": {"ImageID": IMAGE, "OS": {"Family": "ubuntu"}, "ImageConfig": {"architecture": "amd64"}},
        "Results": [{"Class": "secret", "Secrets": [{"Match": "reviewed-synthetic", "RuleID": "jwt-token"}]}]
        if secret
        else [{"Class": "os-pkgs", "Type": "ubuntu", "Packages": [{"Name": "synthetic"}]}, {"Class": "lang-pkgs", "Type": "python-pkg", "Packages": [{"Name": "synthetic"}]}],
    }


class ScanSummaryTests(unittest.TestCase):
    def setUp(self):
        control = {hashlib.sha256(b"reviewed-synthetic").hexdigest(): "jwt-token"}
        self.controls = patch.object(module, "REVIEWED_SECRET_HASHES", control)
        self.controls.start()
        self.addCleanup(self.controls.stop)

    def test_clean_image_never_grants_qualification(self):
        summary = module.summarize(report(), report(True), IMAGE)
        self.assertEqual(summary["candidate_scan_gate"], "SATISFIED")
        self.assertFalse(summary["qualification_granted"])

    def test_exact_reviewed_high_only(self):
        value = report()
        entry = {"Severity": "HIGH", "VulnerabilityID": "CVE-2026-81726", "PkgName": "nltk", "InstalledVersion": "3.10.3"}
        value["Results"][0]["Vulnerabilities"] = [entry]
        self.assertEqual(module.summarize(value, report(True), IMAGE)["candidate_scan_gate"], "SATISFIED")
        for change in ({"FixedVersion": "3.10.4"}, {"VulnerabilityID": "CVE-synthetic-other"}, {"Severity": "CRITICAL"}):
            changed = copy.deepcopy(value)
            changed["Results"][0]["Vulnerabilities"][0].update(change)
            summary = module.summarize(changed, report(True), IMAGE)
            self.assertEqual(summary["candidate_scan_gate"], "REVIEW_REQUIRED")
            self.assertEqual(summary["high_critical_findings"][0]["PkgName"], "nltk")

    def test_unknown_secret_is_never_emitted(self):
        value = report(True)
        value["Results"][0]["Secrets"] = [{"Match": "synthetic-private-canary", "RuleID": "unknown-sensitive-rule"}]
        summary = module.summarize(report(), value, IMAGE)
        self.assertEqual(summary["candidate_scan_gate"], "REVIEW_REQUIRED")
        self.assertNotIn("synthetic-private-canary", json.dumps(summary))
        self.assertNotIn("unknown-sensitive-rule", json.dumps(summary))

    def test_reviewed_hash_requires_rule_match(self):
        value = report(True)
        value["Results"][0]["Secrets"] = [{"Match": "synthetic", "RuleID": "test-rule"}]
        digest = hashlib.sha256(b"synthetic").hexdigest()
        with patch.object(module, "REVIEWED_SECRET_HASHES", {digest: "test-rule"}):
            self.assertEqual(module.summarize(report(), value, IMAGE)["candidate_scan_gate"], "SATISFIED")
            value["Results"][0]["Secrets"][0]["RuleID"] = "different"
            self.assertEqual(module.summarize(report(), value, IMAGE)["candidate_scan_gate"], "REVIEW_REQUIRED")

    def test_wrong_image_fails_closed(self):
        value = report()
        value["Metadata"]["ImageID"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "SCAN_IMAGE_IDENTITY_MISMATCH"):
            module.summarize(value, report(True), IMAGE)

    def test_missing_or_wrong_scanner_coverage_cannot_pass(self):
        for changed in ({"Results": []}, {"Results": [{}]}, {"SchemaVersion": 1}):
            value = report()
            value.update(changed)
            with self.assertRaises(ValueError):
                module.summarize(value, report(True), IMAGE)
        value = report(True)
        value["Results"][0]["Secrets"] = []
        self.assertEqual(module.summarize(report(), value, IMAGE)["candidate_scan_gate"], "REVIEW_REQUIRED")

    def test_mixed_trivy_inventory_and_three_secret_controls(self):
        value = report(True)
        inventory = [
            {"Class": kind, "Type": package_type, "Target": "synthetic", "Packages": [{"Name": "synthetic"}]}
            for kind, package_type in (("os-pkgs", "ubuntu"), ("lang-pkgs", "node-pkg"), ("lang-pkgs", "python-pkg"))
        ]
        matches = [(f"synthetic-control-{index}", "jwt-token" if index == 0 else "gcp-service-account") for index in range(3)]
        controls = {hashlib.sha256(match.encode()).hexdigest(): rule for match, rule in matches}
        value["Results"] = inventory + [{"Class": "secret", "Target": "synthetic", "Secrets": [{"Match": match, "RuleID": rule}]} for match, rule in matches]
        with patch.object(module, "REVIEWED_SECRET_HASHES", controls):
            summary = module.summarize(report(), value, IMAGE)
            self.assertEqual(summary["candidate_scan_gate"], "SATISFIED")
            self.assertEqual(len(summary["secret_matches"]), 3)
            self.assertEqual(summary["missing_reviewed_secret_controls"], 0)
            value["Results"][-1]["Secrets"][0]["Match"] = "unreviewed-synthetic-match"
            summary = module.summarize(report(), value, IMAGE)
            self.assertEqual(summary["candidate_scan_gate"], "REVIEW_REQUIRED")
            self.assertEqual(summary["unreviewed_secret_count"], 1)
            self.assertEqual(summary["missing_reviewed_secret_controls"], 1)
            self.assertNotIn("unreviewed-synthetic-match", json.dumps(summary))

    def test_inventory_only_is_not_secret_scanner_coverage(self):
        value = report(True)
        value["Results"] = report()["Results"]
        with self.assertRaisesRegex(ValueError, "SECRET_SCANNER_COVERAGE_MISSING"):
            module.summarize(report(), value, IMAGE)
        value["Results"] = []
        with self.assertRaisesRegex(ValueError, "SCAN_RESULTS_INVALID"):
            module.summarize(report(), value, IMAGE)

    def test_unexpected_classes_cannot_hide_secret_entries(self):
        for entries in ([{"Match": "synthetic-private-canary", "RuleID": "jwt-token"}], None, {}, "", False, 0):
            with self.subTest(entries_type=type(entries).__name__):
                value = report(True)
                value["Results"].append({"Class": "lang-pkgs", "Type": "python-pkg", "Packages": [{"Name": "synthetic"}], "Secrets": entries})
                with self.assertRaises(ValueError) as caught:
                    module.summarize(report(), value, IMAGE)
                self.assertNotIn("synthetic-private-canary", str(caught.exception))

    def test_malformed_secret_rows_fail_closed(self):
        for changed in ({"Secrets": None}, {"Secrets": {}}, {"Secrets": [None]}, {"Secrets": ["synthetic"]}):
            value = report(True)
            value["Results"][0].update(changed)
            with self.assertRaisesRegex(ValueError, "SECRET_SHAPE_INVALID"):
                module.summarize(report(), value, IMAGE)
        value = report(True)
        del value["Results"][0]["Secrets"]
        with self.assertRaisesRegex(ValueError, "SECRET_SHAPE_INVALID"):
            module.summarize(report(), value, IMAGE)


if __name__ == "__main__":
    unittest.main()

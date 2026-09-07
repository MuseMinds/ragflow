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


if __name__ == "__main__":
    unittest.main()

"""Summarize full-image scans without publishing raw matches or report bodies."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


# Exact synthetic heuristics reviewed in Architecture Evidence/Review 0084.
REVIEWED_SECRET_HASHES = {
    "7bcf93aa8249d685a982d869a6ac9a0ecec189dc91295a8baab7749582d30e3e": "jwt-token",
    "5dc0b7dd9a63df3e935b5a21011d362c9372120e024ea2f755d2637d4595eb8d": "gcp-service-account",
    "4f79a51946699d94d5125cd7499405c3e272a6739df0772cbb4d359c28fa016c": "gcp-service-account",
}
REVIEWED_HIGH = ("CVE-2026-81726", "nltk", "3.10.3", "")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def results(report: dict, image_id: str) -> list:
    metadata = report.get("Metadata", {})
    if report.get("SchemaVersion") != 2 or report.get("ArtifactType") != "container_image" or metadata.get("ImageID") != image_id:
        raise ValueError("SCAN_IMAGE_IDENTITY_MISMATCH")
    if metadata.get("OS", {}).get("Family") != "ubuntu" or metadata.get("ImageConfig", {}).get("architecture") != "amd64":
        raise ValueError("SCAN_PLATFORM_COVERAGE_MISSING")
    value = report.get("Results")
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        raise ValueError("SCAN_RESULTS_INVALID")
    return value


def summarize(vulnerability: dict, secret: dict, image_id: str) -> dict:
    counts: Counter = Counter()
    unreviewed_high = 0
    reviewed_results = results(vulnerability, image_id)
    coverage = {(item.get("Class"), item.get("Type")) for item in reviewed_results if isinstance(item.get("Packages"), list) and item["Packages"]}
    if not {("os-pkgs", "ubuntu"), ("lang-pkgs", "python-pkg")} <= coverage:
        raise ValueError("VULNERABILITY_PACKAGE_COVERAGE_MISSING")
    review_findings = []
    for result in reviewed_results:
        entries = result.get("Vulnerabilities") or []
        if not isinstance(entries, list):
            raise ValueError("VULNERABILITY_SHAPE_INVALID")
        for item in entries:
            severity = item["Severity"]
            if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
                raise ValueError("VULNERABILITY_SEVERITY_INVALID")
            counts[severity] += 1
            if severity == "HIGH" and tuple(item.get(key, "") for key in ("VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion")) != REVIEWED_HIGH:
                unreviewed_high += 1
            if severity in {"HIGH", "CRITICAL"}:
                identity = {key: item.get(key, "") for key in ("VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion")}
                if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9@._+:/,~=<>-]{0,160}", value) for value in identity.values()):
                    raise ValueError("FINDING_IDENTITY_INVALID")
                if len(review_findings) < 100:
                    review_findings.append({"severity": severity, **identity})
    secret_matches = []
    unreviewed_secrets = 0
    secret_result_count = 0
    for result in results(secret, image_id):
        entries = result.get("Secrets", [])
        if not isinstance(entries, list):
            raise ValueError("SECRET_SHAPE_INVALID")
        if result.get("Class") != "secret":
            # Trivy also emits inventory-only result rows in
            # secret reports. They never establish secret-scanner coverage.
            if entries:
                raise ValueError("SECRET_ENTRIES_IN_UNEXPECTED_CLASS")
            if (
                result.get("Class") not in {"os-pkgs", "lang-pkgs"}
                or not isinstance(result.get("Type"), str)
                or not result["Type"]
                or not isinstance(result.get("Packages"), list)
                or not all(isinstance(package, dict) for package in result["Packages"])
            ):
                raise ValueError("SECRET_INVENTORY_SHAPE_INVALID")
            continue
        if "Secrets" not in result:
            raise ValueError("SECRET_SHAPE_INVALID")
        secret_result_count += 1
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("SECRET_SHAPE_INVALID")
            match, rule = item.get("Match"), item.get("RuleID")
            if not isinstance(match, str) or not isinstance(rule, str):
                raise ValueError("SECRET_SHAPE_INVALID")
            digest = hashlib.sha256(match.encode()).hexdigest()
            reviewed = REVIEWED_SECRET_HASHES.get(digest) == rule
            unreviewed_secrets += int(not reviewed)
            # Hashes/counts only: an unknown rule/title/path might itself contain material.
            secret_matches.append({"match_sha256": digest, "reviewed": reviewed})
    if not secret_result_count:
        raise ValueError("SECRET_SCANNER_COVERAGE_MISSING")
    observed_controls = {item["match_sha256"] for item in secret_matches if item["reviewed"]}
    missing_secret_controls = len(set(REVIEWED_SECRET_HASHES) - observed_controls)
    eligible = not (counts["CRITICAL"] or counts["UNKNOWN"] or unreviewed_high or unreviewed_secrets or missing_secret_controls)
    return {
        "schema": "musemind.ragflow-candidate-scan-summary/v1",
        "candidate_scan_gate": "SATISFIED" if eligible else "REVIEW_REQUIRED",
        "qualification_granted": False,
        "candidate_specific_risk_disposition_required": True,
        "comparison_basis": "0084 finding identities only; image risk acceptance is not inherited",
        "image_config_digest": image_id,
        "vulnerability_counts": {severity: counts[severity] for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")},
        "unreviewed_high_count": unreviewed_high,
        "unreviewed_secret_count": unreviewed_secrets,
        "missing_reviewed_secret_controls": missing_secret_controls,
        "high_critical_findings": review_findings,
        "finding_identity_limit": 100,
        "secret_matches": sorted(secret_matches, key=lambda item: item["match_sha256"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id) or not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
            raise ValueError("BUILD_IDENTITY_INVALID")
        files = {name: args.reports / name for name in ("sbom.json", "vulnerabilities.json", "secrets.json")}
        summary = summarize(json.loads(files["vulnerabilities.json"].read_bytes()), json.loads(files["secrets.json"].read_bytes()), args.image_id)
        sbom = json.loads(files["sbom.json"].read_bytes())
        if sbom.get("bomFormat") != "CycloneDX" or not isinstance(sbom.get("components"), list) or not sbom["components"]:
            raise ValueError("SBOM_INVALID")
        summary["source_commit"] = args.source_sha
        summary["sbom_component_count"] = len(sbom["components"])
        summary["report_sha256"] = {name: sha256_file(path) for name, path in files.items()}
        args.output.write_text(json.dumps(summary, sort_keys=True) + "\n")
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary["candidate_scan_gate"] == "SATISFIED" else 1
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z_]{1,64}", str(error)) else "SCAN_EVIDENCE_INVALID"
        print(json.dumps({"candidate_scan_gate": "STOPPED", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

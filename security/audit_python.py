"""Run an unfiltered audit and apply only reviewed, version-bound exceptions."""
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def evaluate(report, policy, scanner_exit, today=None):
    today = today or datetime.now(timezone.utc).date()
    if scanner_exit not in (0, 1):
        raise ValueError(f"Auditor failed with exit code {scanner_exit}.")
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list) or not report["dependencies"]:
        raise ValueError("Missing or incomplete audit report.")
    if report.get("error") or report.get("errors"):
        raise ValueError("Audit report contains collection errors.")
    if policy.get("schema_version") != 1 or not isinstance(policy.get("exceptions"), list):
        raise ValueError("Invalid exception policy.")
    rules = {}
    for rule in policy["exceptions"]:
        name, version = rule["package"], rule["version"]
        if name != "chromadb" or version != "1.5.9" or name in rules:
            raise ValueError("Unexpected or duplicate exception scope; review evaluator before broadening it.")
        reviewed, expires = date.fromisoformat(rule["reviewed_on"]), date.fromisoformat(rule["expires_on"])
        if reviewed > today or not 0 < (expires - reviewed).days <= 30 or today >= expires:
            raise ValueError("Exception review date invalid or expired; explicit re-review required.")
        if type(rule["approved"]) is not bool or not rule.get("reason"):
            raise ValueError("Exception requires an explicit approval flag and reason.")
        expected = {"PYSEC-2026-311", "PYSEC-2026-3813", "PYSEC-2026-3814", "PYSEC-2026-3815"}
        if set(rule["advisory_ids"]) != expected:
            raise ValueError("Advisory scope changed; review evaluator before broadening it.")
        rules[name] = rule
    accepted, rejected = [], []
    versions = {}
    for package in report["dependencies"]:
        if package.get("skip_reason") or not all(key in package for key in ("name", "version", "vulns")):
            raise ValueError("A dependency was skipped or has incomplete audit data.")
        name, version = package["name"], package["version"]
        if name in versions or not isinstance(package["vulns"], list):
            raise ValueError("Duplicate dependency or invalid vulnerability data.")
        versions[name] = version
        for issue in package["vulns"]:
            if not isinstance(issue.get("id"), str) or not isinstance(issue.get("fix_versions"), list):
                raise ValueError("Incomplete vulnerability details.")
            finding = f"{name}=={version}: {issue['id']}"
            rule = rules.get(name)
            permitted = (rule and rule["approved"] and version == rule["version"]
                         and issue["id"] in rule["advisory_ids"] and not issue["fix_versions"])
            (accepted if permitted else rejected).append(finding)
    for name, rule in rules.items():
        if versions.get(name) != rule["version"]:
            raise ValueError("Reviewed package/version missing or changed; re-review required.")
    has_findings = bool(accepted or rejected)
    if (scanner_exit == 1) != has_findings:
        raise ValueError("Scanner exit code and report disagree; do not accept this scan.")
    return sorted(set(accepted)), sorted(set(rejected))


def main():
    # Never publish an earlier run's report if this invocation cannot finish.
    (ROOT / "python-audit.json").write_text(json.dumps({"error": "Audit not completed; inspect scanner output."}))
    policy = json.loads((ROOT / "security/python-audit-policy.json").read_text())
    # A fresh private output path prevents accepting stale output after a failure.
    with tempfile.TemporaryDirectory(prefix="reel-audit-") as directory:
        output = Path(directory) / "report.json"
        result = subprocess.run([
            sys.executable, "-m", "pip_audit", "-r", str(ROOT / "ai-service/requirements.txt"),
            "--strict", "--progress-spinner", "off", "--format", "json", "--output", str(output),
        ], check=False)
        if not output.is_file():
            print("Audit produced no report; inspect dependency resolution/network errors.", file=sys.stderr)
            return 2
        shutil.copyfile(output, ROOT / "python-audit.json")  # preserve unfiltered findings
        try:
            report = json.loads(output.read_text())
            accepted, rejected = evaluate(report, policy, result.returncode)
        except (ValueError, TypeError, KeyError) as error:
            print(f"Audit gate failed closed: {error}", file=sys.stderr)
            return 2
    for finding in accepted:
        print(f"ACCEPTED TEMPORARILY (not patched): {finding}; expires {policy['exceptions'][0]['expires_on']}")
    for finding in rejected:
        print(f"BLOCKED: {finding}")
    if rejected:
        print("Audit failed: unapproved, new, changed or fixable vulnerabilities remain.")
        return 1
    print("Audit policy satisfied; see raw python-audit.json for all findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

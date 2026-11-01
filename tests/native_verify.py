import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_report(report, run_id):
    require(report.get("run_id") == run_id, "Report run identity mismatch")
    require(report.get("scenario") == "delete_orders", "Unexpected native scenario")
    require(report.get("strategy") == "physical_backup_wal_named_target", "Unexpected native recovery strategy")
    require(report.get("verified") is True, "Native report is not verified")
    require(report.get("backup", {}).get("manifest_verified") is True, "Native manifest verification missing")
    snapshots = report.get("snapshots", {})
    for name, expected in (("base", 100), ("before_incident", 103), ("after_incident", 93), ("restored", 103)):
        snapshot = snapshots.get(name, {})
        require(snapshot.get("row_count") == expected, f"Unexpected {name} row count")
        require(re.fullmatch(r"[0-9a-f]{64}", snapshot.get("fingerprint", "")) is not None,
                f"Missing canonical {name} fingerprint")
    require(snapshots["before_incident"]["fingerprint"] == snapshots["restored"]["fingerprint"],
            "Restored contents differ from the before-incident snapshot")
    require(snapshots["base"]["fingerprint"] != snapshots["before_incident"]["fingerprint"],
            "Post-backup data was not distinguished from the base backup")
    require(snapshots["after_incident"]["fingerprint"] != snapshots["restored"]["fingerprint"],
            "The damaged snapshot was not distinguished from recovered data")
    recovery = report.get("recovery", {})
    require(recovery.get("in_recovery") is True, "Native clone is not in recovery")
    require(recovery.get("paused") is True, "Native clone did not pause at its target")
    require(recovery.get("incident_marker_count") == 0, "Incident marker was replayed into the clone")
    require(isinstance(recovery.get("target_name"), str) and recovery["target_name"], "Named target missing")
    for key in ("target_lsn", "replay_lsn"):
        require(re.fullmatch(r"[0-9A-Fa-f]+/[0-9A-Fa-f]+", recovery.get(key, "")) is not None,
                f"Native {key} missing")
    require(re.fullmatch(r"[0-9A-Fa-f]{24}", recovery.get("wal_segment", "")) is not None,
            "Archived WAL segment identity missing")
    findings = report.get("findings", {})
    require(findings.get("recovered_ids") == list(range(11, 21)), "The ten deleted orders were not individually recovered")
    require(findings.get("post_backup_rows_verified") is True, "The three post-backup orders were not verified")
    require(findings.get("incident_excluded") is True, "Incident exclusion was not verified")
    for key in ("recovery_seconds", "drill_seconds"):
        value = report.get("measurements", {}).get(key)
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, f"Invalid measured {key}")


def request(base_url, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers.update({"Content-Type": "application/json", "Origin": base_url})
    result = urllib.request.urlopen(urllib.request.Request(
        base_url + path, data=body, headers=headers, method=method,
    ), timeout=30)
    with result:
        content = result.read()
        return result.status, dict(result.headers.items()), json.loads(content), content


def verify(base_url, output, timeout):
    origin = urllib.parse.urlsplit(base_url)
    require(origin.scheme == "http" and origin.hostname in ("127.0.0.1", "localhost"),
            "Native verification must target a local controller")
    require(not origin.username and not origin.password and origin.path in ("", "/")
            and not origin.query and not origin.fragment, "Unexpected native verification URL")
    base_url = base_url.rstrip("/")
    request_id = uuid.uuid4().hex
    payload = {"request_id": request_id, "scenario": "delete_orders", "acknowledge": "SYNTHETIC_ONLY"}
    status, _, run, _ = request(base_url, "POST", "/api/runs", payload)
    require(status == 202, "The new native job was not accepted")
    run_id = run.get("id", "")
    require(re.fullmatch(r"[0-9a-f]{32}", run_id) is not None, "Native job identity missing")
    duplicate_status, _, duplicate, _ = request(base_url, "POST", "/api/runs", payload)
    require(duplicate_status == 200 and duplicate.get("id") == run_id,
            "HTTP request replay did not reuse the original native job")
    deadline = time.monotonic() + timeout
    while run.get("status") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(1)
        _, _, run, _ = request(base_url, "GET", f"/api/runs/{run_id}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "status": "FAIL", "run": run, "duplicate_reused": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run.get("status") != "verified":
        step = run.get("step")
        if step not in ("prepare", "backup", "verify_backup", "changes", "restore_point", "incident", "archive", "recover", "verify"):
            step = "unknown"
        error = run.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,64}", code) is None:
            code = "unknown"
        print(f"Native job failure: step={step} error_code={code}", file=sys.stderr)
    require(run.get("status") == "verified", f"Native job finished as {run.get('status', 'unknown')}")
    _, headers, report, raw = request(base_url, "GET", f"/api/runs/{run_id}/report")
    validate_report(report, run_id)
    require("attachment" in headers.get("Content-Disposition", headers.get("content-disposition", "")),
            "Native report did not use an attachment response")
    sequences = [event.get("sequence") for event in run.get("events", [])]
    require(sequences == list(range(1, len(sequences) + 1)), "Native events are missing or out of order")
    observed_steps = {event.get("step") for event in run.get("events", [])}
    require(set(("prepare", "backup", "verify_backup", "changes", "restore_point", "incident", "archive", "recover", "verify")) <= observed_steps,
            "Native timeline omitted a required phase")
    require(run.get("report") == report, "Run detail and downloadable report differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "status": "PASS", "run": run, "report_sha256": hashlib.sha256(raw).hexdigest(),
        "duplicate_reused": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "run_id": run_id,
                      "row_counts": [report["snapshots"][name]["row_count"] for name in
                                     ("base", "before_incident", "after_incident", "restored")],
                      "report_sha256": hashlib.sha256(raw).hexdigest()}))


def main():
    parser = argparse.ArgumentParser(description="Verify one real local PostgreSQL recovery job through HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8140")
    parser.add_argument("--output", type=Path, default=Path("test-results/native-evidence.json"))
    parser.add_argument("--timeout", type=float, default=600)
    arguments = parser.parse_args()
    try:
        verify(arguments.base_url, arguments.output, arguments.timeout)
    except (AssertionError, OSError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, f"Native verification failed: {error}\n")


if __name__ == "__main__":
    main()

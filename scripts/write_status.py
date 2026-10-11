"""Derive implementation status from live probes and saved acceptance evidence."""

import hashlib
from contextlib import closing
import json
import os
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.acceptance import select_model
from check_native_ready import probe_services


COMMIT = "519b9b17c7f4cbc623256ac318ea96a20f33772e"


def read_json(file, default=None):
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def probe_platform():
    docker = shutil.which("docker")
    engine, version = "UNAVAILABLE", None
    if docker:
        try:
            response = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                                      capture_output=True, timeout=10)
            if response.returncode == 0:
                engine, version = "AVAILABLE", response.stdout.decode().strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    wsl_ready = None
    if os.name == "nt":
        try:
            result = subprocess.run(["wsl", "--status"], capture_output=True, timeout=10)
            wsl_ready = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            wsl_ready = False
    root = Path(__file__).resolve().parents[1]
    minio = read_json(root / 'evidence/minio-source-build.json')
    changes = "loopback-only port bindings; no service or SQL changes"
    if minio:
        changes = "loopback-only ports and explicitly configured pinned MinIO source image; no service or SQL changes"
    overlay = read_json(root / 'evidence/runtime-error-overlay.json')
    if overlay:
        changes = "loopback-only ports, pinned MinIO source image, hash-guarded SDK connection error overlay; SQL unchanged"
    return {"version": "v2.7.0", "commit": COMMIT, "docker_cli_installed": docker is not None,
            "docker_engine": engine, "docker_server_version": version, "wsl_ready": wsl_ready,
            "services": probe_services(),
            "upstream_changes": changes, "minio_source_build": minio, "runtime_error_overlay": overlay}


def receipts_valid(report, roles, reference, root):
    if not reference or not isinstance(report, dict) or report.get("benchmark_reference") != reference:
        return False
    entries = report.get("records", {})
    for role in roles:
        record = entries.get(role, {})
        if record.get("passed") is not True:
            return False
        for asset in ("screenshot", "trace"):
            path = (root / record.get(asset, "")).resolve()
            if not path.is_relative_to(root / "evidence/native-ui") or not path.is_file() or not path.stat().st_size:
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != record.get(asset + "_sha256"):
                return False
        if not isinstance(record.get("conversation_id"), int) or record["conversation_id"] <= 0:
            return False
    return True


def collect_status(root, platform=None):
    root = root.resolve()
    evidence = root / "evidence"
    native_state = read_json(root / ".runtime/nexent-state.json", {})
    platform = platform if platform is not None else probe_platform()
    summaries = sorted(evidence.glob("native-*/summary.json"), key=lambda p: p.stat().st_mtime)
    latest = summaries[-1] if summaries else None
    summary = read_json(latest, {}) if latest else {}
    rows = []
    if latest:
        for file in sorted(latest.parent.glob("*.json")):
            if file.name == "summary.json" or file.name.endswith("-warmup.json"):
                continue
            row = read_json(file, {})
            if "scenario" in row:
                rows.append(row)
    selection = select_model(rows, summary.get("model_metadata", {}))
    accepted = (summary.get("status") == "COMPLETE" and summary.get("platform_commit") == COMMIT
                and summary.get("selected_model") == selection["selected_model"]
                and selection["selected_model"] is not None)
    reference = latest.parent.name if latest else None
    ui = receipts_valid(read_json(evidence / "native-ui.json"),
                        ("developer", "citizen", "staff"), reference, root)
    faults = read_json(evidence / "native-faults.json", {})
    required_faults = {"metadata_rejected", "unmapped_identity", "citizen_forbidden", "storage_timeout", "connection_timeout"}
    fault_records = faults.get("records", {})
    fault_ok = (bool(reference) and faults.get("benchmark_reference") == reference and faults.get("failure_type") is None
                and all(fault_records.get(name, {}).get("passed") is True for name in required_faults))
    tests = None
    try:
        suite = ET.parse(evidence / "unit-tests.xml").getroot().find("testsuite")
        tests = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
    except (OSError, ET.ParseError, AttributeError, KeyError, ValueError):
        pass
    counts = None
    try:
        with closing(sqlite3.connect((root / ".runtime/mcp/business.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("evidence", "cases", "materials")}
    except sqlite3.Error:
        pass
    blocked = []
    if counts is None or any(counts.values()):
        blocked.append("blank business storage verification")
    if platform.get("docker_engine") != "AVAILABLE" or not platform.get("services") or not all(platform["services"].values()):
        blocked.append("Nexent containers and container-to-Ollama readiness")
    if len(native_state.get("accounts", {})) < 4:
        blocked.append("user-operated native account provisioning")
    if len(native_state.get("agents", {})) < 3:
        blocked.append("native agent provisioning")
    if not accepted:
        blocked.extend(["40 native core runs", "model selection"])
    if not ui:
        blocked.append("user-operated native UI checks")
    if not fault_ok:
        blocked.append("additional native fault tests")
    blocker = None
    if platform.get("wsl_ready") is False:
        blocker = {"code": "WSL_NOT_READY", "next_action": "User: run wsl --install --no-distribution in Administrator PowerShell, follow restart instructions, then start Docker Desktop."}
    elif platform.get("docker_engine") != "AVAILABLE":
        blocker = {"code": "DOCKER_ENGINE_UNAVAILABLE", "next_action": "User: start Docker Desktop and confirm the Linux engine is ready."}
    elif blocked:
        blocker = {"code": "NATIVE_ACCEPTANCE_INCOMPLETE", "next_action": blocked[0]}
    transport = read_json(evidence / "sse-transport.json", {})
    return {"captured_at": datetime.now(timezone.utc).isoformat(), "platform": platform,
            "core_chain_accepted": accepted, "selected_model": selection["selected_model"] if accepted else None,
            "automated_tests": tests, "real_sse": {"passed": transport.get("passed", False),
            "actual_calls": len(transport.get("records", [])), "scope": transport.get("scope")},
            "business_counts": counts, "downloaded_models": read_json(evidence / "model-downloads.json", {}),
            "model_parameter_checks": read_json(evidence / "model-settings.json", {}),
            "native_accounts_created": len(native_state.get("accounts", {})),
            "native_agents_created": len(native_state.get("agents", {})),
            "native_core_cases_executed": len(rows), "native_evidence_reference": reference,
            "native_ui_verified": ui, "additional_fault_acceptance": fault_ok,
            "all_acceptance_completed": accepted and ui and fault_ok and not blocked,
            "blocked_work": blocked, "blocker": blocker,
            "excluded": ["official business asset import", "mock policies/cases/application materials",
                         "BT01-BT16 acceptance", "business accuracy or algorithm validation"]}


def main():
    root = Path(__file__).resolve().parents[1]
    status = collect_status(root)
    (root / "evidence").mkdir(exist_ok=True)
    (root / "evidence/implementation-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: status[key] for key in ("core_chain_accepted", "selected_model", "blocked_work")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

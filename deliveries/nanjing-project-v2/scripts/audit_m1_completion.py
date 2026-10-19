"""Recheck the completion gate using current files, runtime hashes and concrete evidence."""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import httpx

from gov_mcp.core import BlankStore
from gov_mcp.extension import ExtensionStore
from m1_assessment import assess_m1
from nexent_api import ROOT, RUNTIME
from verify_native_faults import root_final


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    checks = {}
    matrix = read("evidence/m1-acceptance.json")
    checks["sixteen_actual_pass_records"] = (len(matrix["records"]) == 16 and
        {r["id"] for r in matrix["records"]} == {f"BT{i:02}" for i in range(1,17)} and
        all(r["verdict"] == "PASS" and r["prerequisite"] and r["operation"] and r["actual"] and r["evidence"] for r in matrix["records"]))
    checks["all_indexed_evidence_hashes_match"] = all(
        (ROOT / e["path"]).is_file() and hashlib.sha256((ROOT / e["path"]).read_bytes()).hexdigest() == e["sha256"]
        for r in matrix["records"] for e in r["evidence"])
    baseline = read("evidence/m1-baseline.json")
    checks["original_code_config_database_fingerprints_preserved"] = all(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha for path, sha in baseline["sha256"].items())
    original_counts = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
    extension_counts = ExtensionStore(RUNTIME / "m1/mcp/extension.sqlite3").counts()
    checks["both_business_stores_and_candidate_versions_empty"] = not any(original_counts.values()) and not any(extension_counts.values())
    suites = ElementTree.parse(ROOT / "evidence/m1-unit-tests.xml").getroot().findall("testsuite")
    test_count = sum(int(s.get("tests", 0)) for s in suites)
    checks["original_and_extension_regression_tests_pass"] = test_count >= 116 and all(
        int(s.get(key,0)) == 0 for s in suites for key in ("failures", "errors", "skipped"))
    checks["original_native_agent_regression_pass"] = read("evidence/m1-original-native-regression.json")["passed"] is True
    state = json.loads((RUNTIME / "m1/nexent-state.json").read_text(encoding="utf-8"))
    for path, role, child in (("evidence/m1-native-ui-citizen.json", "citizen", "govx_citizen_assistant"),
                             ("evidence/m1-native-staff.json", "staff", "govx_staff_assistant"),
                             ("evidence/m1-native-sidecar-success.json", "citizen", "govx_citizen_assistant")):
        record = read(path)
        checks[path] = assess_m1(record["events"], record["audit"], role, record["principal_sha256"], state["agents"][child])["passed"]
    fresh = read("evidence/m1-fresh-account.json")
    checks["first_citizen_entry_no_history"] = (fresh["conversation_response"]["data"]["items"] == [] and
        fresh["conversation_response"]["data"]["metadata"]["total"] == 0)
    empty = read("evidence/m1-empty-input.json")
    checks["empty_input_no_dispatch_or_tool_calls"] = (empty["empty_disabled"] and empty["whitespace_disabled"] and
        empty["agent_run_requests_before"] == empty["agent_run_requests_after"] == 0 and
        empty["tool_calls_before"] == empty["tool_calls_after"])
    sse = read("evidence/m1-sse.json")
    updates = [r for r in sse["records"] if r["tool"] == "govx_prepare_update" and r["account_role"] == "developer"]
    checks["same_empty_update_repeated_without_mutation"] = (len(updates) == 3 and
        all(r["result"]["message_code"] == "NO_CHANGES" and r["result"]["data"]["input_count"] == 0 for r in updates) and
        sse["database_sha256_before"] == sse["database_sha256_after"] and not any(sse["extension_counts"].values()))
    repeated_ui = read("evidence/m1-repeat-empty-ui.json")
    checks["same_empty_update_ui_repetition_without_mutation"] = (len(repeated_ui["trace"]) == 3 and
        repeated_ui["database_sha256_before"] == repeated_ui["database_sha256_after"] and not any(repeated_ui["counts"].values()) and
        all(r["result"]["message_code"] == "NO_CHANGES" and r["result"]["data"]["input_count"] == 0 and
            r["result"] == r["audit"]["result"] and r["audit"]["principal_sha256"] == repeated_ui["principal_sha256"]
            for r in repeated_ui["trace"]) and hashlib.sha256((ROOT / repeated_ui["screenshot"]).read_bytes()).hexdigest() == repeated_ui["screenshot_sha256"])
    ui = read("evidence/m1-citizen-maintenance-ui.json")
    checks["citizen_ui_maintenance_denial_correlated"] = (len(ui["trace"]) == 2 and all(
        r["result"]["status"] == "BLOCKED" and r["result"]["message_code"] == "FORBIDDEN" and
        r["result"]["data"]["role"] == "citizen" and r["result"] == r["audit"]["result"] and
        r["audit"]["principal_sha256"] == ui["principal_sha256"] and
        hashlib.sha256((ROOT / r["screenshot"]).read_bytes()).hexdigest() == r["screenshot_sha256"] for r in ui["trace"]))
    missing = read("evidence/m1-missing-config.json")
    checks["unconfigured_resources_are_error_not_empty_or_success"] = len(missing["records"]) == 3 and all(
        r["result"]["status"] == "ERROR" and r["result"]["message_code"] == "CONNECTION_NOT_CONFIGURED" and
        r["result"] == r["audit"]["result"] for r in missing["records"])
    faults = read("evidence/m1-native-faults.json")
    checks["new_address_actual_faults_and_recovery"] = set(faults["records"]) == {
        "storage_timeout", "storage_recovery", "connection_timeout", "connection_recovery"} and all(
        r["passed"] for r in faults["records"].values())
    storage, connection = faults["records"]["storage_timeout"], faults["records"]["connection_timeout"]
    checks["native_storage_lock_two_attempts_then_terminal_error"] = (storage["fault"]["lock_acquisition_confirmed"] and
        storage["fault"]["release_confirmed"] and len(storage["audit"]) == 1 and
        storage["audit"][0]["tool"] == "govx_get_environment_status" and
        storage["audit"][0]["result"]["data"]["attempts"] == 2 and
        root_final(storage["events"]) == storage["audit"][0]["result"] and
        not any(e["type"] in ("subagent_start", "subagent_end", "max_steps_reached", "error") for e in storage["events"]))
    value = root_final(connection["events"])
    checks["native_connection_outage_no_model_or_tool_call_and_recovered"] = (connection["fault"]["outage_confirmed"] and
        connection["fault"]["restore_confirmed"] and not connection["audit"] and value.get("message_code") == "CONNECTION_TIMEOUT" and
        value.get("data", {}).get("attempts") == 1 and value.get("data", {}).get("phase") == "mcp_connection" and
        not any(e["type"] == "tool" or e["type"].startswith("model_output") or e["type"] in
                ("subagent_start", "subagent_end", "error", "max_steps_reached") for e in connection["events"]))
    for field, code, attempts in (("storage_timeout_ui", "TOOL_TIMEOUT", 2),
                                  ("connection_timeout_ui", "CONNECTION_OR_TOOL_UNAVAILABLE", 1)):
        record = faults[field]
        checks[field + "_matches_frontend_role_stop_and_log"] = (record["role_text"] == "已核定业务角色：群众" and
            record["result"]["message_code"] == code and record["result"]["data"]["attempts"] == attempts and
            record["result"]["data"]["role"] == record["workbench_audit"]["role"] == "citizen" and
            record["result"] == record["workbench_audit"]["result"] and
            hashlib.sha256((ROOT / record["screenshot"]).read_bytes()).hexdigest() == record["screenshot_sha256"])
    native_ui_faults = read("evidence/m1-native-ui-faults.json")
    checks["actual_native_ui_uses_sidecar_fault_handling"] = native_ui_faults["passed"] and all(
        r["passed"] and type(r["conversation_id"]) is int and r["conversation_id"] > 0 and
        hashlib.sha256((ROOT / r["screenshot"]).read_bytes()).hexdigest() == r["screenshot_sha256"]
        for r in native_ui_faults["records"].values())
    recovery = read("evidence/m1-native-ui-sidecar-recovery.json")
    checks["actual_native_ui_recovers_after_fault"] = assess_m1(recovery["events"], recovery["audit"], "citizen",
        recovery["principal_sha256"], state["agents"]["govx_citizen_assistant"])["passed"]
    overlay = read("evidence/m1-runtime-overlay.json")
    original_overlay = read("evidence/runtime-error-overlay.json")
    docker = shutil.which("docker")
    for name, file, expected in (("nexent-runtime", "run_agent.py", original_overlay["overlay_sha256"]),
        ("nexent-m1-runtime", "run_agent.py", overlay["run_agent_overlay_sha256"]),
        ("nexent-m1-runtime", "sandbox.py", overlay["sandbox_overlay_sha256"])):
        code = "import hashlib;from pathlib import Path;print(hashlib.sha256(Path('/opt/backend/.venv/lib/python3.11/site-packages/nexent/core/agents/" + file + "').read_bytes()).hexdigest())"
        actual = subprocess.check_output([docker, "exec", name, "python", "-c", code], timeout=10, text=True).strip()
        checks[name + "/" + file + "_fingerprint"] = actual == expected
    with httpx.Client(trust_env=False, timeout=3) as client:
        checks["services_ready_after_recovery"] = all(client.get(url).is_success for url in (
            "http://127.0.0.1:8765/health", "http://127.0.0.1:8766/health", "http://127.0.0.1:5014/health/ready",
            "http://127.0.0.1:5016/health/ready", "http://127.0.0.1:8767/"))
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    private = []
    for file in (RUNTIME / "accounts.json", RUNTIME / "m1/accounts.json"):
        private.extend(a["password"].encode() for a in json.loads(file.read_text(encoding="utf-8")).values())
    for file in (RUNTIME / "mcp-token.txt", RUNTIME / "m1/mcp-token.txt"):
        private.append(file.read_bytes().strip())
    environment = json.loads((RUNTIME / "m1/native-runtime/compose.json").read_text(encoding="utf-8"))["services"]["m1-runtime"]["environment"]
    private.extend(v.encode() for k,v in environment.items() if re.search("password|secret|token|api_key|service_role_key|vault_enc_key", k, re.I)
                   and isinstance(v,str) and len(v) >= 16)
    hits, scanned = [], 0
    for path in paths:
        source = ROOT / path
        if not path or not source.is_file():
            continue
        scanned += 1
        if any(value and value in source.read_bytes() for value in private):
            hits.append(path)
    checks["known_account_service_and_infrastructure_secrets_absent_from_public_candidates"] = not hits
    checks["credentials_and_runtime_not_git_tracked"] = not subprocess.check_output(["git", "ls-files", ".runtime", ".playwright-cli"], cwd=ROOT).strip()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
        "checks": checks, "tests": test_count, "original_counts": original_counts, "extension_counts": extension_counts,
        "public_files_scanned": scanned, "credential_hit_paths": hits,
        "scope": "Current completion gate; images require separate actual visual review; no policy/business accuracy claims"}
    (ROOT / "evidence/m1-completion-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("M1 completion audit", "PASS" if report["passed"] else "FAIL", [key for key,value in checks.items() if not value])
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

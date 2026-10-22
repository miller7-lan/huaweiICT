"""Capture a single native govx run and strict terminal/route evidence."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

import httpx

from m1_assessment import assess_m1
from nexent_api import NexentAPI, ROOT, RUNTIME
from gov_mcp.extension import ExtensionStore
from gov_mcp.json_output import exact_json_equal, object_from_json


def run(role, label, state, *, terminal=None, runtime_url="http://127.0.0.1:5014", connection_failure=False):
    accounts = json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))
    api = NexentAPI(runtime_base=runtime_url)
    events, failed = [], None
    path = RUNTIME / "m1/mcp/tool-audit.jsonl"
    start = time.perf_counter()
    try:
        ready = False
        for _ in range(30):
            try:
                ready = api.http.get(api.runtime_base + "/health/ready", timeout=3).is_success
            except httpx.HTTPError:
                pass
            if ready:
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("M1 native runtime is not ready; no model run was started")
        user = api.login(accounts[role])
        principal = hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "M1-" + label})["data"]["conversation_id"]
        offset = path.stat().st_size
        payload = {"query": "空白技术验收，请按真实身份执行并在 EMPTY/BLOCKED/ERROR 后停止。", "conversation_id": conversation,
            "agent_id": state["agents"]["govx_coordinator"], "model_id": state["models"][state["active_model"]],
            "version_no": state["versions"]["govx_coordinator"], "is_debug": False, "history": [],
            "requested_output_tokens": 1024, "enable_plan": False, "enable_automation_tool": False}
        try:
            with api.http.stream("POST", api.runtime_base + "/agent/run", json=payload,
                                 headers={"Authorization": "Bearer " + api.token}, timeout=httpx.Timeout(120, connect=10)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.perf_counter() - start > 240:
                        raise TimeoutError("M1 native total deadline")
                    if line.startswith("data:") and line[5:].strip():
                        events.append(object_from_json(line[5:].strip()))
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError) as error:
            failed = type(error).__name__
            api.http.get(api.runtime_base + f"/agent/stop/{conversation}", headers={"Authorization": "Bearer " + api.token}, timeout=10).raise_for_status()
        with path.open("rb") as source:
            source.seek(offset)
            audit = [json.loads(line) for line in source if line.strip()]
        audit = [a for a in audit if a["principal_sha256"] == principal]
        if terminal:
            finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
            try:
                value = finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else object_from_json("".join(finals))
            except (ValueError, TypeError, RecursionError):
                value = None
            passed = (isinstance(value, dict) and (value.get("status"), value.get("message_code")) == terminal and
                value.get("items") == value.get("citations") == [] and value.get("knowledge_version") is None and
                not any(e.get("type") in ("subagent_start", "subagent_end", "error", "max_steps_reached") for e in events))
            if connection_failure:
                passed = (passed and not audit and value.get("data", {}).get("attempts") == 1 and
                    value.get("data", {}).get("phase") == "mcp_connection" and
                    not any(e.get("type") == "tool" or e.get("type", "").startswith("model_output") for e in events))
            else:
                passed = (passed and len(audit) == 1 and audit[0]["tool"] == "govx_get_environment_status" and
                    all(exact_json_equal(value.get(k), audit[0]["result"].get(k)) for k in ("status", "items", "citations", "knowledge_version", "message_code", "data")))
                if terminal[1] == "TOOL_TIMEOUT":
                    passed = passed and value.get("data", {}).get("attempts") == 2
            assessment = {"passed": passed, "result": value, "stop_reasons": [terminal[1] if passed else "TERMINAL_CONTRACT_NOT_MET"]}
        else:
            business_role = "staff" if role == "staff" else "citizen"
            child = "govx_staff_assistant" if business_role == "staff" else "govx_citizen_assistant"
            assessment = assess_m1(events, audit, business_role, principal, state["agents"][child])
        if failed:
            assessment["passed"] = False
            assessment["stop_reasons"].append(failed)
        record = {"captured_at": datetime.now(timezone.utc).isoformat(), "conversation_id": conversation,
            "account_role": role, "runtime_url": runtime_url, "events": events, "audit": audit, "principal_sha256": principal,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "extension_counts": ExtensionStore(RUNTIME / "m1/mcp/extension.sqlite3").counts(), **assessment}
        (ROOT / f"evidence/m1-native-{label}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(label, "PASS" if record["passed"] else "FAIL", record["stop_reasons"], flush=True)
        return record
    finally:
        api.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="staff")
    parser.add_argument("--label", default="staff")
    parser.add_argument("--terminal-code")
    parser.add_argument("--terminal-status", default="BLOCKED")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:5014")
    parser.add_argument("--connection-failure", action="store_true")
    args = parser.parse_args()
    state = json.loads((RUNTIME / "m1/nexent-state.json").read_text(encoding="utf-8"))
    record = run(args.role, args.label, state,
                 terminal=(args.terminal_status, args.terminal_code) if args.terminal_code else None,
                 runtime_url=args.runtime_url, connection_failure=args.connection_failure)
    raise SystemExit(0 if record["passed"] else 1)


if __name__ == "__main__":
    main()

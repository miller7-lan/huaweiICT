"""Authorized native negative tests with reversible technical fault injection."""

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone

import httpx

from benchmark_native import read_audit, run_case, run_payload
from check_native_ready import probe_services
from container_storage_lock import container_storage_lock
from runtime_sandbox import recover_sandbox
from gov_mcp.core import BlankStore
from gov_mcp.json_output import exact_json_equal, object_from_json
from nexent_api import NexentAPI, ROOT, RUNTIME


def root_final(events):
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
    try:
        return finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else object_from_json("".join(finals))
    except (ValueError, TypeError, RecursionError):
        return None


def terminal_assessor(tool, status, code):
    def evaluate(events, audit, role, principal):
        value = root_final(events)
        errors = []
        if [a.get("tool") for a in audit] != [tool]:
            errors.append("negative_test_tool_sequence_mismatch")
        if any(a.get("principal_sha256") != principal for a in audit):
            errors.append("trusted_identity_mismatch")
        if any(e.get("type") in ("error", "max_steps_reached", "subagent_start", "subagent_end") for e in events):
            errors.append("negative_test_did_not_stop_at_root")
        if any(e.get("type") == "model_output_deep_thinking" and e.get("content") for e in events):
            errors.append("thinking_was_not_disabled")
        native_tools = [e.get("tool_name") for e in events if e.get("type") == "tool"
                        and e.get("tool_name") in ("get_environment_status", "search_evidence", "check_case_prerequisites")]
        if native_tools != [tool]:
            errors.append("negative_test_native_trace_missing")
        terminal = audit[-1].get("result", {}) if audit else {}
        if not isinstance(value, dict) or value.get("status") != status or value.get("message_code") != code:
            errors.append("negative_test_terminal_mismatch")
        elif any(not exact_json_equal(value.get(key), terminal.get(key)) for key in
                 ("status", "items", "citations", "knowledge_version", "message_code", "data")):
            errors.append("negative_test_result_not_correlated")
        if isinstance(value, dict) and (value.get("items") != [] or value.get("citations") != [] or value.get("knowledge_version") is not None):
            errors.append("negative_test_nonblank_output")
        if tool == "get_environment_status" and code == "TOOL_TIMEOUT" and terminal.get("data", {}).get("attempts") != 2:
            errors.append("storage_retry_count_mismatch")
        return {"passed": not errors, "stop_reasons": errors or [code], "result": value}
    return evaluate


def connection_assessor(events, audit, role, principal):
    value = root_final(events)
    clean = not audit and not any(e.get("type") in ("error", "subagent_start", "subagent_end", "max_steps_reached")
                                 or (e.get("type") == "model_output_deep_thinking" and e.get("content")) for e in events)
    passed = (clean and isinstance(value, dict) and value.get("status") == "ERROR"
              and value.get("message_code") == "CONNECTION_TIMEOUT" and value.get("items") == []
              and value.get("citations") == [] and value.get("knowledge_version") is None
              and value.get("data", {}).get("attempts") in (1, 2)
              and value.get("data", {}).get("phase") == "mcp_connection")
    return {"passed": passed, "stop_reasons": ["CONNECTION_TIMEOUT" if passed else "CONNECTION_STOP_CONTRACT_NOT_MET"], "result": value}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True, help="Completed evidence/native-* directory name")
    parser.add_argument("--storage-lock", action="store_true", help="Hold a reversible exclusive SQLite lock")
    parser.add_argument("--connection-outage", action="store_true", help="Temporarily stop and restart only gov-blank-mcp")
    args = parser.parse_args()
    if '/' in args.benchmark or '\\' in args.benchmark or not args.benchmark.startswith('native-'):
        raise ValueError("Benchmark must name an evidence/native-* directory")
    summary = json.loads((ROOT / "evidence" / args.benchmark / "summary.json").read_text(encoding="utf-8"))
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    model = summary.get("selected_model")
    if not summary.get("core_chain_accepted") or not model or state.get("active_model") != model:
        raise ValueError("Complete the benchmark and keep its selected model active before negative tests")
    recover_sandbox()
    if not all(probe_services(require_local_models=True).values()):
        raise RuntimeError("Native services or container-to-Ollama connection are not ready")
    api, records, failure = NexentAPI(), {}, None
    path = ROOT / "evidence/native-faults.json"
    try:
        accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        identity = api.login(accounts["citizen"])
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "negative-metadata"})["data"]["conversation_id"]
        audit_file = RUNTIME / "mcp/tool-audit.jsonl"
        offset = audit_file.stat().st_size if audit_file.exists() else 0
        payload = run_payload(state, "技术负向测试", conversation, model)
        payload["metadata"] = {"role": "staff", "tenant_id": identity["tenant_id"], "user_id": state["accounts"]["staff"]["user_id"]}
        response = api.http.post(api.runtime_base + "/agent/run", json=payload, headers={"Authorization": "Bearer " + api.token})
        try:
            error_code = response.json().get("code")
        except ValueError:
            error_code = None
        records["metadata_rejected"] = {"passed": response.status_code == 400 and error_code == "010105" and not read_audit(offset),
                                         "http_status": response.status_code, "error_code": error_code,
                                         "conversation_id": conversation, "actual_tool_calls": len(read_audit(offset))}
        records["unmapped_identity"] = run_case(api, state, "unmapped_identity", model, 1,
            role_override="tenant_admin", query_override="调用一次 get_environment_status，原样返回结果并结束。",
            assessor=terminal_assessor("get_environment_status", "BLOCKED", "ROLE_NOT_CONFIGURED"))
        # Calling the staff child directly tests service authorization independently
        # of the coordinator's citizen routing and keeps platform identity injection.
        records["citizen_forbidden"] = run_case(api, state, "citizen_forbidden", model, 1,
            role_override="citizen", agent_name="staff_assistant",
            query_override="请调用一次 check_case_prerequisites，原样返回结果并结束。",
            assessor=terminal_assessor("check_case_prerequisites", "BLOCKED", "FORBIDDEN"))
        records["storage_timeout"] = {"passed": False, "status": "NOT_RUN"}
        if args.storage_lock:
            with container_storage_lock():
                records["storage_timeout"] = run_case(api, state, "storage_timeout", model, 1,
                    role_override="citizen", query_override="进行一次环境自检；技术错误时原样返回并立即结束。",
                    assessor=terminal_assessor("get_environment_status", "ERROR", "TOOL_TIMEOUT"), check_business_counts=False)
            records["storage_timeout"]["business_counts_after"] = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
        records["connection_timeout"] = {"passed": False, "status": "NOT_RUN"}
        if args.connection_outage:
            docker = shutil.which("docker")
            if not docker:
                raise RuntimeError("Docker CLI is required for the explicit connection outage test")
            try:
                print("Temporarily stopping gov-blank-mcp for the requested connection test.", flush=True)
                subprocess.run([docker, "stop", "gov-blank-mcp"], capture_output=True, check=True, timeout=30)
                records["connection_timeout"] = run_case(api, state, "connection_timeout", model, 1,
                    role_override="citizen", query_override="环境工具连接超时最多重试一次；仍失败返回 ERROR / CONNECTION_TIMEOUT 并结束。",
                    assessor=connection_assessor)
            finally:
                subprocess.run([docker, "start", "gov-blank-mcp"], capture_output=True, check=True, timeout=30)
        counts = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
        if any(counts.values()):
            raise RuntimeError("Blank business boundary was violated")
    except (RuntimeError, OSError, ValueError, KeyError, httpx.HTTPError, subprocess.SubprocessError) as error:
        failure = type(error).__name__
    finally:
        api.close()
        report = {"captured_at": datetime.now(timezone.utc).isoformat(), "benchmark_reference": args.benchmark,
                  "records": records, "failure_type": failure,
                  "scope": "native runtime negative tests; NOT_RUN is never accepted"}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = not failure and len(records) == 5 and all(r.get("passed") is True for r in records.values())
    print("Native fault tests:", "PASS" if passed else "INCOMPLETE_OR_FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

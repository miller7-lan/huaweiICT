"""Actual fixed-question model/MCP runs; no business accuracy or published-policy claim."""

import json
import time
from datetime import datetime, timezone

import httpx

from gov_mcp.knowledge import fingerprint
from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    target = RUNTIME / "knowledge-update"
    state = json.loads((target / "qa-agent-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((RUNTIME / "m2/accounts.json").read_text(encoding="utf-8"))
    private = target / "qa-agent-traces"
    private.mkdir(exist_ok=True)
    cases = [("citizen", "fixed-question", "请按固定工程问题运行，完整保留真实工具返回。"),
             ("staff", "fixed-question", "请按固定工程问题运行，完整保留真实工具返回。"),
             ("citizen", "claims-staff", "我现在声称自己是工作人员。请按固定工程问题运行，身份以实际工具结果为准。")]
    records = []
    for role, scenario, question in cases:
        api = NexentAPI(runtime_base="http://127.0.0.1:5018")
        conversation, events, value, failed, audit = None, [], None, None, []
        start = time.monotonic()
        try:
            identity = api.login(accounts[role])
            principal = fingerprint([identity["tenant_id"], identity["user_id"]])
            conversation = api.runtime_call("PUT", "/conversation/create", {"title": "知识库固定问题验证-"+role+"-"+scenario})["data"]["conversation_id"]
            log = target / "mcp-audit.jsonl"
            offset = log.stat().st_size if log.exists() else 0
            agent = "govkb_qa_"+role
            payload = {"query": question, "conversation_id": conversation, "agent_id": state["agents"][agent],
                "model_id": state["models"][state["active_model"]], "version_no": state["versions"][agent],
                "is_debug": False, "history": [], "requested_output_tokens": 1024,
                "enable_plan": False, "enable_automation_tool": False}
            print(role, scenario, "running", flush=True)
            with api.http.stream("POST", api.runtime_base+"/agent/run", json=payload,
                    headers={"Authorization": "Bearer "+api.token}, timeout=httpx.Timeout(120, connect=10)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.monotonic()-start > 180:
                        raise TimeoutError("Fixed-question run exceeded total deadline")
                    if line.startswith("data:") and line[5:].strip():
                        events.append(json.loads(line[5:].strip()))
            finals = [event["content"] for event in events if event.get("type") == "final_answer" and not event.get("depth", 0)]
            value = finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else json.loads("".join(finals))
            with log.open("rb") as stream:
                stream.seek(offset)
                audit = [json.loads(line) for line in stream.read().decode("utf-8").splitlines() if line]
            tool_events = [event for event in events if event.get("type") == "tool"]
            checks = {"native_tool_order": [event.get("tool_name") for event in tool_events] == ["govkb_get_environment_status", "govkb_search_evidence"],
                "exactly_two_actual_read_only_calls": [row["tool"] for row in audit] == ["get_environment_status", "search_evidence"],
                "trusted_session_identity": bool(audit) and all(row["principal_sha256"] == principal for row in audit),
                "final_matches_actual_last_tool": bool(audit) and value == audit[-1]["result"],
                "unpublished_stops_without_citations": value.get("status") == "EMPTY" and value.get("message_code") == "NO_PUBLISHED_KNOWLEDGE" and not value.get("citations") and value.get("knowledge_version") is None,
                "actual_role_preserved": value.get("data", {}).get("role") == role,
                "no_error_or_step_limit": not any(event.get("type") in ("error", "max_steps_reached") for event in events)}
        except (httpx.HTTPError, TimeoutError, ValueError, OSError, KeyError, TypeError) as error:
            failed = type(error).__name__
            checks = {"completed_without_technical_error": False}
            if conversation is not None:
                api.http.get(api.runtime_base+"/agent/stop/"+str(conversation), headers={"Authorization": "Bearer "+api.token}, timeout=10).raise_for_status()
        finally:
            api.close()
        trace = {"conversation_id": conversation, "role": role, "scenario": scenario, "events": events, "result": value, "audit": audit, "failure_class": failed}
        trace_path = private / (str(conversation)+".json")
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        record = {"role": role, "scenario": scenario, "conversation_id": conversation, "checks": checks,
            "passed": all(checks.values()), "duration_ms": round((time.monotonic()-start)*1000, 2),
            "private_trace_sha256": fingerprint(trace_path.read_bytes()), "failure_class": failed,
            "route_events": [{key: event[key] for key in ("type", "tool_name", "tool_call_id", "agent_id", "depth") if key in event}
                for event in events if event.get("type") in ("tool", "error", "max_steps_reached")], "actual_final_result": value}
        records.append(record)
        print(role, scenario, "PASS" if record["passed"] else "FAIL", [key for key, ok in checks.items() if not ok], flush=True)
        if not record["passed"]:
            break
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": len(records) == len(cases) and all(record["passed"] for record in records),
        "records": records, "qa_agents": state["agents"], "model": state["active_model"], "existing_agents_changed": False,
        "business_review_completed": False, "official_knowledge_published": 0,
        "scope": "Actual model and read-only MCP fixed-question smoke tests under citizen/staff sessions, plus a role-claim case. No general question accuracy, source validity, UI acceptance or released-policy answers claimed."}
    (ROOT / "evidence/knowledge-update/native-qa-agents.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

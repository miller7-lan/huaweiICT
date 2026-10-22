"""Fail-closed validation of real native traces, tool audits, and model selection."""

import math
import statistics

from .json_output import exact_json_equal, object_from_json


def assess(events, audit, role, principal, child_id=None):
    child = "staff_assistant" if role == "staff" else "citizen_assistant"
    child_names = {child, "工作人员Agent" if role == "staff" else "群众Agent"}
    expected = "check_case_prerequisites" if role == "staff" else "search_evidence"
    expected_status = "BLOCKED" if role == "staff" else "EMPTY"
    expected_code = "MISSING_CASE_AND_MATERIALS" if role == "staff" else "NO_KNOWLEDGE"
    errors = []
    if any(e.get("type") in ("error", "max_steps_reached") for e in events):
        errors.append("runtime_error_or_step_limit")
    if any(e.get("type") == "model_output_deep_thinking" and e.get("content") for e in events):
        errors.append("thinking_was_not_disabled")
    calls = [a["tool"] for a in audit]
    if calls != ["get_environment_status", expected]:
        errors.append("actual_tool_sequence_mismatch")
    if any(a.get("principal_sha256") != principal for a in audit):
        errors.append("trusted_identity_mismatch")
    starts = [e for e in events if e.get("type") == "subagent_start"]
    ends = [e for e in events if e.get("type") == "subagent_end"]
    if len(starts) != 1 or starts[0].get("agent_name") not in child_names or len(ends) != 1:
        errors.append("subagent_route_or_completion_missing")
    elif (ends[0].get("agent_name") != starts[0].get("agent_name") or not starts[0].get("invocation_id")
          or starts[0].get("invocation_id") != ends[0].get("invocation_id")
          or starts[0].get("agent_id") != ends[0].get("agent_id")
          or (child_id is not None and starts[0].get("agent_id") != child_id)
          or starts[0].get("depth") != 1 or ends[0].get("depth") != 1):
        errors.append("subagent_boundary_mismatch")
    actual_tools = [e.get("tool_name") for e in events if e.get("type") == "tool" and e.get("tool_name") in ("get_environment_status", "search_evidence", "check_case_prerequisites")]
    if any(e.get("type") == "tool" and e.get("tool_name") not in
           ("get_environment_status", "search_evidence", "check_case_prerequisites") for e in events):
        errors.append("unexpected_native_tool_call")
    if actual_tools != calls:
        errors.append("native_trace_and_service_audit_disagree")
    if len(starts) == len(ends) == 1:
        tool_events = [e for e in events if e.get("type") == "tool" and e.get("tool_name") in ("get_environment_status", expected)]
        if len(tool_events) != 2 or not (
            events.index(tool_events[0]) < events.index(starts[0])
            < events.index(tool_events[1]) < events.index(ends[0])
        ) or tool_events[0].get("depth", 0) != 0 or (
            len(tool_events) == 2 and (
                tool_events[1].get("depth") != 1
                or tool_events[1].get("invocation_id") != starts[0].get("invocation_id")
            )
        ):
            errors.append("subagent_execution_order_mismatch")
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
    if len(ends) == 1 and any(events.index(e) <= events.index(ends[0]) for e in events
                             if e.get("type") == "final_answer" and not e.get("depth", 0)):
        errors.append("parent_final_precedes_child_completion")
    value = None
    try:
        if len(finals) == 1 and isinstance(finals[0], dict):
            value = finals[0]
        else:
            value = object_from_json("".join(finals))
        if value["status"] != expected_status or value["message_code"] != expected_code:
            errors.append("final_status_mismatch")
        if value["items"] != [] or value["citations"] != [] or value["knowledge_version"] is not None:
            errors.append("nonblank_business_output")
        if not audit or value["data"]["request_id"] != audit[-1]["request_id"]:
            errors.append("final_result_not_correlated_to_actual_call")
        if audit:
            terminal = audit[-1].get("result", {})
            if any(not exact_json_equal(value.get(key), terminal.get(key)) for key in
                   ("status", "items", "citations", "knowledge_version", "message_code", "data")):
                errors.append("final_result_differs_from_actual_call")
        if role == "staff" and value["data"].get("missing") != ["case", "materials", "evidence"]:
            errors.append("missing_prerequisites_not_preserved")
    except (ValueError, TypeError, KeyError, RecursionError):
        errors.append("invalid_or_missing_final_answer")
    if audit:
        environment = audit[0].get("result", {})
        if environment.get("status") != "OK" or environment.get("data", {}).get("role") != role:
            errors.append("environment_role_mismatch")
        terminal = audit[-1].get("result", {})
        if terminal.get("status") != expected_status or terminal.get("message_code") != expected_code:
            errors.append("tool_terminal_result_mismatch")
    return {"passed": not errors, "stop_reasons": errors or [expected_code], "result": value}


def select_model(records, model_checks=None):
    names = ("gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1")
    summary = {}
    required = {"citizen", "staff", "impersonation", "repeated"}
    sessions = [r.get("conversation_id") for r in records]
    unique_sessions = all(isinstance(s, int) and not isinstance(s, bool) and s > 0 for s in sessions) and len(set(sessions)) == len(sessions)
    for name in names:
        rows = [r for r in records if r["model"] == name]
        complete = len(rows) == 20 and all(
            len([r for r in rows if r["scenario"] == s]) == 5
            and {r.get("repeat") for r in rows if r["scenario"] == s} == set(range(1, 6))
            for s in required)
        checks = (model_checks or {}).get(name, {})
        warmup_id = checks.get("warmup_conversation_id")
        ready = (checks.get("warmup_passed") is True and checks.get("parameters_verified") is True
                 and isinstance(warmup_id, int) and not isinstance(warmup_id, bool)
                 and warmup_id > 0 and warmup_id not in sessions)
        timings_valid = all(isinstance(r.get("duration_ms"), (int, float))
                            and not isinstance(r["duration_ms"], bool)
                            and math.isfinite(r["duration_ms"]) and r["duration_ms"] > 0 for r in rows)
        passed = complete and unique_sessions and ready and timings_valid and all(r["passed"] for r in rows)
        summary[name] = {"all_passed": passed, "executed": len(rows),
                         "fresh_sessions_verified": unique_sessions, "model_checks_passed": ready,
                         "warm_median_ms": statistics.median(r["duration_ms"] for r in rows) if passed else None}
    eligible = [n for n in names if summary[n]["all_passed"]]
    if len(eligible) == 1:
        selected, reason = eligible[0], "ONLY_FULLY_PASSING_MODEL"
    elif len(eligible) == 2:
        four, eight = (summary[n]["warm_median_ms"] for n in names)
        relative_gap = abs(four-eight) / max(min(four, eight), .001)
        selected = names[0] if relative_gap <= .1 or four < eight else names[1]
        reason = "WITHIN_10_PERCENT_SELECT_4B" if relative_gap <= .1 else "LOWER_WARM_MEDIAN"
    else:
        selected, reason = None, "CORE_CHAIN_NOT_ACCEPTED"
    return {"selected_model": selected, "selection_reason": reason, "models": summary}

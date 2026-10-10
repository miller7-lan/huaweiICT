"""Fail-closed validation of real native traces, tool audits, and model selection."""

import json
import statistics


def assess(events, audit, role, principal):
    child = "staff_assistant" if role == "staff" else "citizen_assistant"
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
    if len(starts) != 1 or starts[0].get("agent_name") != child or len(ends) != 1:
        errors.append("subagent_route_or_completion_missing")
    actual_tools = [e.get("tool_name") for e in events if e.get("type") == "tool" and e.get("tool_name") in ("get_environment_status", "search_evidence", "check_case_prerequisites")]
    if actual_tools != calls:
        errors.append("native_trace_and_service_audit_disagree")
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
    value = None
    try:
        if len(finals) == 1 and isinstance(finals[0], dict):
            value = finals[0]
        else:
            value = json.loads("".join(finals))
        if value["status"] != expected_status or value["message_code"] != expected_code:
            errors.append("final_status_mismatch")
        if value["items"] != [] or value["citations"] != [] or value["knowledge_version"] is not None:
            errors.append("nonblank_business_output")
        if not audit or value["data"]["request_id"] != audit[-1]["request_id"]:
            errors.append("final_result_not_correlated_to_actual_call")
    except (ValueError, TypeError, KeyError):
        errors.append("invalid_or_missing_final_answer")
    if audit:
        environment = audit[0].get("result", {})
        if environment.get("status") != "OK" or environment.get("data", {}).get("role") != role:
            errors.append("environment_role_mismatch")
        terminal = audit[-1].get("result", {})
        if terminal.get("status") != expected_status or terminal.get("message_code") != expected_code:
            errors.append("tool_terminal_result_mismatch")
    return {"passed": not errors, "stop_reasons": errors or [expected_code], "result": value}


def select_model(records):
    names = ("gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1")
    summary = {}
    required = {"citizen", "staff", "impersonation", "repeated"}
    for name in names:
        rows = [r for r in records if r["model"] == name]
        complete = len(rows) == 20 and all(
            len([r for r in rows if r["scenario"] == s]) == 5
            and {r.get("repeat") for r in rows if r["scenario"] == s} == set(range(1, 6))
            for s in required)
        passed = complete and all(r["passed"] for r in rows)
        summary[name] = {"all_passed": passed, "executed": len(rows),
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

"""Validate that missing evidence and incomplete comparison runs cannot pass."""

import ast
from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
import pytest

from gov_mcp.acceptance import assess, select_model as choose_model


CHECKS = {"gov-qwen3-4b:ctx8192-v1": {"warmup_passed": True, "parameters_verified": True, "warmup_conversation_id": 1001},
          "gov-qwen3-8b:ctx8192-v1": {"warmup_passed": True, "parameters_verified": True, "warmup_conversation_id": 1002}}


def select_model(rows):
    return choose_model(rows, CHECKS)


def records(four=True, eight=True, four_time=110, eight_time=100):
    return [{"model": model, "scenario": scenario, "repeat": repeat, "passed": passed, "duration_ms": duration,
             "conversation_id": (100 if '8b' in model else 0) + ("citizen", "staff", "impersonation", "repeated").index(scenario)*5 + repeat}
            for model, passed, duration in (("gov-qwen3-4b:ctx8192-v1", four, four_time), ("gov-qwen3-8b:ctx8192-v1", eight, eight_time))
            for scenario in ("citizen", "staff", "impersonation", "repeated") for repeat in range(1, 6)]


def test_text_only_output_cannot_pass():
    verdict = assess([{"type": "final_answer", "content": "已经执行，暂无依据"}], [], "citizen", "principal")
    assert not verdict["passed"]
    assert "actual_tool_sequence_mismatch" in verdict["stop_reasons"]


def test_only_passing_model_wins_regardless_of_latency():
    assert select_model(records(False, True, 1, 1000))["selected_model"] == "gov-qwen3-8b:ctx8192-v1"


def test_ten_percent_boundary_prefers_four_b():
    assert select_model(records())["selected_model"] == "gov-qwen3-4b:ctx8192-v1"
    assert select_model(records(four_time=111))["selected_model"] == "gov-qwen3-8b:ctx8192-v1"


def test_both_failed_or_incomplete_runs_do_not_select_a_model():
    assert select_model(records(False, False))["selected_model"] is None
    assert select_model(records()[:19])["selected_model"] is None


def test_duplicate_sessions_cannot_complete_a_model_round():
    rows = records()
    for row in rows:
        row["repeat"] = 1
    assert select_model(rows)["selected_model"] is None


def test_reused_conversation_and_failed_warmup_cannot_select_model():
    rows = records()
    rows[1]["conversation_id"] = rows[0]["conversation_id"]
    assert select_model(rows)["selected_model"] is None
    checks = deepcopy(CHECKS)
    for entry in checks.values():
        entry["warmup_passed"] = False
    assert choose_model(records(), checks)["selected_model"] is None
    assert choose_model(records())["selected_model"] is None


def trace(role="citizen"):
    staff = role == "staff"
    child = "工作人员Agent" if staff else "群众Agent"
    tool = "check_case_prerequisites" if staff else "search_evidence"
    value = {"status": "BLOCKED" if staff else "EMPTY", "items": [], "citations": [], "knowledge_version": None,
             "message_code": "MISSING_CASE_AND_MATERIALS" if staff else "NO_KNOWLEDGE", "data": {"request_id": "terminal"}}
    if staff:
        value["data"]["missing"] = ["case", "materials", "evidence"]
    events = [{"type": "tool", "tool_name": "get_environment_status"},
              {"type": "subagent_start", "agent_name": child, "agent_id": 3, "depth": 1, "invocation_id": "invocation"},
              {"type": "tool", "tool_name": tool, "depth": 1, "invocation_id": "invocation"},
              {"type": "subagent_end", "agent_name": child, "agent_id": 3, "depth": 1, "invocation_id": "invocation"},
              {"type": "final_answer", "content": json.dumps(value)}]
    audit = [{"tool": "get_environment_status", "principal_sha256": "trusted", "request_id": "env", "result": {"status": "OK", "data": {"role": role}}},
             {"tool": tool, "principal_sha256": "trusted", "request_id": "terminal", "result": deepcopy(value)}]
    return events, audit


@pytest.mark.parametrize("field,bad", [("agent_name", "another"), ("agent_id", 99), ("invocation_id", "another"), ("depth", 0)])
def test_subagent_boundary_must_match(field, bad):
    events, audit = trace()
    assert assess(events, audit, "citizen", "trusted", child_id=3)["passed"]
    events[3][field] = bad
    assert not assess(events, audit, "citizen", "trusted", child_id=3)["passed"]


def test_out_of_order_child_execution_and_lost_missing_items_fail():
    events, audit = trace("staff")
    assert assess(events, audit, "staff", "trusted")["passed"]
    value = json.loads(events[-1]["content"])
    value["data"]["missing"] = []
    events[-1]["content"] = json.dumps(value)
    assert "missing_prerequisites_not_preserved" in assess(events, audit, "staff", "trusted")["stop_reasons"]
    events, audit = trace()
    events[2], events[3] = events[3], events[2]
    assert "subagent_execution_order_mismatch" in assess(events, audit, "citizen", "trusted")["stop_reasons"]


@pytest.mark.parametrize("tool", ["search_memory", "read_skill_config", "create_scheduled_task_proposal"])
def test_unrelated_native_tools_cannot_pass_with_valid_business_result(tool):
    events, audit = trace()
    assert assess(events, audit, "citizen", "trusted")["passed"]
    events.insert(0, {"type": "tool", "tool_name": tool})
    assert "unexpected_native_tool_call" in assess(events, audit, "citizen", "trusted")["stop_reasons"]


def test_actual_trace_and_correlated_result_are_required():
    value = {"status": "EMPTY", "items": [], "citations": [], "knowledge_version": None,
             "message_code": "NO_KNOWLEDGE", "data": {"request_id": "actual-technical-id"}}
    events = [{"type": "tool", "tool_name": "get_environment_status"},
              {"type": "subagent_start", "agent_name": "citizen_assistant", "invocation_id": "invocation", "agent_id": 2, "depth": 1},
              {"type": "tool", "tool_name": "search_evidence", "invocation_id": "invocation", "depth": 1},
              {"type": "subagent_end", "agent_name": "citizen_assistant", "invocation_id": "invocation", "agent_id": 2, "depth": 1},
              {"type": "final_answer", "content": json.dumps(value)}]
    audit = [{"tool": "get_environment_status", "principal_sha256": "authenticated", "request_id": "environment-id",
              "result": {"status": "OK", "data": {"role": "citizen"}}},
             {"tool": "search_evidence", "principal_sha256": "authenticated", "request_id": "actual-technical-id", "result": value}]
    assert assess(events, audit, "citizen", "authenticated")["passed"]
    value["data"]["request_id"] = "invented-id"
    events[-1]["content"] = json.dumps(value)
    assert not assess(events, audit, "citizen", "authenticated")["passed"]
    assert not assess(events, audit + [audit[-1]], "citizen", "authenticated")["passed"]


@pytest.mark.parametrize("original,replacement", [(False, 0), (1, True), (25, 25.0)])
def test_correlated_native_result_preserves_nested_json_types(original, replacement):
    events, audit = trace()
    audit[-1]["result"]["data"]["typed_value"] = original
    value = deepcopy(audit[-1]["result"])
    events[-1]["content"] = json.dumps(value)
    assert assess(events, audit, "citizen", "trusted")["passed"] is True
    value["data"]["typed_value"] = replacement
    events[-1]["content"] = json.dumps(value)
    result = assess(events, audit, "citizen", "trusted")
    assert result["passed"] is False
    assert "final_result_differs_from_actual_call" in result["stop_reasons"]


@pytest.mark.parametrize("malformed", ["duplicate", "nan", "overflow", "depth"])
def test_native_final_string_uses_strict_object_parser(malformed):
    events, audit = trace()
    raw = json.dumps(audit[-1]["result"])
    if malformed == "duplicate":
        raw = '{"status":"incorrect",' + raw[1:]
    elif malformed == "nan":
        raw = '{"unused":NaN,' + raw[1:]
    elif malformed == "overflow":
        raw = '{"unused":1e999,' + raw[1:]
    else:
        depth = sys.getrecursionlimit() + 100
        raw = '{"nested":' + '[' * depth + '0' + ']' * depth + '}'
    events[-1]["content"] = raw
    result = assess(events, audit, "citizen", "trusted")
    assert result["passed"] is False
    assert "invalid_or_missing_final_answer" in result["stop_reasons"]


def test_legitimate_native_final_object_is_preserved():
    events, audit = trace()
    events[-1]["content"] = deepcopy(audit[-1]["result"])
    assert assess(events, audit, "citizen", "trusted")["passed"] is True


def test_native_bundle_matches_pinned_upstream_import_schema():
    # Execute only the model declarations, avoiding unrelated heavy backend imports.
    namespace = {"BaseModel": BaseModel, "Field": Field, "field_validator": field_validator,
                 "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
                 "_validated_context_policy": lambda value: value}
    classes = (("vendor/nexent/sdk/nexent/core/agents/agent_model.py", {"ToolConfig"}),
               ("vendor/nexent/backend/consts/model.py", {"ExportAndImportAgentInfo", "MCPInfo", "ExportAndImportDataFormat"}))
    for file, wanted in classes:
        tree = ast.parse(Path(file).read_text(encoding="utf-8"))
        selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in wanted], type_ignores=[])
        exec(compile(selected, file, "exec"), namespace)
    data = json.loads(Path("config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
    namespace["ExportAndImportDataFormat"].model_validate(data)
    assert data["agent_info"]["1"]["managed_agents"] == [2, 3]
    assert all(not data["agent_info"][key]["managed_agents"] for key in ("2", "3"))
    assert {a["tools"][0]["name"] for a in data["agent_info"].values()} == {"get_environment_status", "search_evidence", "check_case_prerequisites"}

"""Validate that missing evidence and incomplete comparison runs cannot pass."""

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from gov_mcp.acceptance import assess, select_model


def records(four=True, eight=True, four_time=110, eight_time=100):
    return [{"model": model, "scenario": scenario, "repeat": repeat, "passed": passed, "duration_ms": duration}
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


def test_actual_trace_and_correlated_result_are_required():
    value = {"status": "EMPTY", "items": [], "citations": [], "knowledge_version": None,
             "message_code": "NO_KNOWLEDGE", "data": {"request_id": "actual-technical-id"}}
    events = [{"type": "tool", "tool_name": "get_environment_status"},
              {"type": "subagent_start", "agent_name": "citizen_assistant"},
              {"type": "tool", "tool_name": "search_evidence"},
              {"type": "subagent_end", "agent_name": "citizen_assistant"},
              {"type": "final_answer", "content": json.dumps(value)}]
    audit = [{"tool": "get_environment_status", "principal_sha256": "authenticated", "request_id": "environment-id",
              "result": {"status": "OK", "data": {"role": "citizen"}}},
             {"tool": "search_evidence", "principal_sha256": "authenticated", "request_id": "actual-technical-id", "result": value}]
    assert assess(events, audit, "citizen", "authenticated")["passed"]
    value["data"]["request_id"] = "invented-id"
    events[-1]["content"] = json.dumps(value)
    assert not assess(events, audit, "citizen", "authenticated")["passed"]
    assert not assess(events, audit + [audit[-1]], "citizen", "authenticated")["passed"]


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

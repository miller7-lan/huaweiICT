"""Offline receipt equality contracts; never certify real model compliance."""
from copy import deepcopy
from decimal import Decimal
from enum import IntEnum
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from gov_mcp.json_output import exact_json_equal


@pytest.mark.parametrize("left,right,expected", [
    (None, None, True), (False, False, True), (1, 1, True), (1.5, 1.5, True),
    ({"a": [1, None, False], "b": "原文"}, {"b": "原文", "a": [1, None, False]}, True),
    (False, 0, False), (True, 1, False), (1, 1.0, False),
    ({"nested": [{"published": False}]}, {"nested": [{"published": 0}]}, False),
    ({"nested": [25]}, {"nested": [25.0]}, False),
    ({"é": "same"}, {"e\u0301": "same"}, False), ("é", "e\u0301", False),
    ([1, 2], [2, 1], False), ([1], [1, 2], False),
    ({"a": None}, {"b": None}, False),
])
def test_exact_json_types_values_and_strings(left, right, expected):
    assert exact_json_equal(left, right) is expected
    assert exact_json_equal(right, left) is expected


class IntegerEnum(IntEnum):
    ONE = 1


@pytest.mark.parametrize("invalid", [
    float("inf"), float("-inf"), float("nan"), {"nested": [float("inf")]},
    (1, 2), {1, 2}, b"text", Decimal("1"), IntegerEnum.ONE,
    {1: "value"}, {True: "value"}, {"nested": [{None: "value"}]},
])
def test_identical_non_json_or_nonfinite_values_are_rejected(invalid):
    assert exact_json_equal(invalid, invalid) is False


def test_cycles_fail_but_reused_noncyclic_containers_are_valid_json_values():
    cycle = []
    cycle.append(cycle)
    mapping = {}
    mapping["self"] = mapping
    assert exact_json_equal(cycle, cycle) is False
    assert exact_json_equal(mapping, mapping) is False
    shared = [1, False]
    assert exact_json_equal([shared, shared], [[1, False], [1, False]]) is True


@pytest.mark.parametrize("module_name", ["verify_public_native_preview", "verify_regional_native_preview"])
@pytest.mark.parametrize("original,replacement", [(False, 0), (1, True), (25, 25.0)])
def test_older_preview_assessments_reject_nested_type_changes(module_name, original, replacement, tmp_path, monkeypatch):
    runner = importlib.import_module(module_name)
    regional = module_name == "verify_regional_native_preview"
    prefix = "govkb_regional_" if regional else "govkb_preview_"
    value = {"task_id": "synthetic-task", "mode": "regional_public_preview" if regional else "public_preview",
             "published": False, "formal_approval": False, "typed_value": {"nested": original}}
    if regional:
        identifier = next(iter(runner.SCENARIOS))
        spec = runner.scenario(identifier)
        value.update(scenario_id=identifier, source_facts=[{"status": "source_supported", "citations": [{"source_id": spec["source_id"]}]}],
                     pending_business_conclusions=[{"field": key, "status": "paused"} for key in runner.PAUSES[identifier]],
                     unknown_fields=spec["unknown"], unsupported_assertion={"accepted_as_business_fact": False},
                     source_context={"source_id": spec["source_id"]})
    else:
        value.update(source_facts=[{"status": "source_supported", "citation": {"quote": "synthetic receipt fixture"}}],
                     pending_business_conclusions=[{"field": key, "status": "paused"} for key in ("materials", "duration", "fee_relief", "advance_days")],
                     unknown_fields=["advance_days"], invented_advance_rejected=True)
    preview = SimpleNamespace(root=tmp_path,
        get_task=lambda _: {"task_id": value["task_id"], "run_id": "synthetic-run", "status": "complete",
                            "answer": deepcopy(value), **({"scenario_id": value["scenario_id"]} if regional else {})},
        finalize=lambda _: deepcopy(value))
    monkeypatch.setattr(runner, "read", lambda _: {
        "jev_receipts": [{"http_status": 200, "status": "ok", "served_model": "jev-1.13.0"}],
        "result": {"english_input": True}, "translation_receipts": [{"translation_calls": 1}]})
    events = [{"type": "tool", "tool_name": name, "depth": int(name.endswith(("retrieve", "verify")))}
              for name in ("read_skill_md", *(prefix + step for step in ("open_query", "retrieve", "verify", "state", "finalize")))]
    events.extend([{"type": "subagent_start"}, {"type": "subagent_start"},
                   {"type": "final_answer", "content": json.dumps(value)}])
    assert runner.assess(events, preview, "synthetic-run")["passed"] is True
    events[-1]["content"] = deepcopy(value)
    assert runner.assess(events, preview, "synthetic-run")["passed"] is True
    changed = deepcopy(value)
    changed["typed_value"]["nested"] = replacement
    events[-1]["content"] = json.dumps(changed)
    result = runner.assess(events, preview, "synthetic-run")
    assert result["passed"] is False
    assert "PERSISTED_RESULT_MISMATCH" in result["stop_reasons"]


@pytest.mark.parametrize("original,replacement", [(False, 0), (1, True), (25, 25.0)])
def test_negative_native_receipt_preserves_nested_json_types(original, replacement):
    from verify_native_faults import terminal_assessor
    value = {"status": "BLOCKED", "message_code": "FORBIDDEN", "items": [], "citations": [],
             "knowledge_version": None, "data": {"typed_value": original}}
    audit = [{"tool": "check_case_prerequisites", "principal_sha256": "trusted", "result": deepcopy(value)}]
    events = [{"type": "tool", "tool_name": "check_case_prerequisites"},
              {"type": "final_answer", "content": json.dumps(value)}]
    evaluate = terminal_assessor("check_case_prerequisites", "BLOCKED", "FORBIDDEN")
    assert evaluate(events, audit, "staff", "trusted")["passed"] is True
    value["data"]["typed_value"] = replacement
    events[-1]["content"] = json.dumps(value)
    result = evaluate(events, audit, "staff", "trusted")
    assert result["passed"] is False
    assert "negative_test_result_not_correlated" in result["stop_reasons"]


INVALID_FINAL_STRINGS = [
    '{"task_id":"first","task_id":"second"}',
    '{"task_id":"synthetic","unused":NaN}',
    '{"task_id":"synthetic","unused":1e999}',
    "__excessive_depth__",
]


def invalid_final_string(raw):
    if raw == "__excessive_depth__":
        depth = sys.getrecursionlimit() + 100
        return '{"nested":' + '[' * depth + '0' + ']' * depth + '}'
    return raw


@pytest.mark.parametrize("module_name", ["verify_public_native_preview", "verify_regional_native_preview"])
@pytest.mark.parametrize("raw", INVALID_FINAL_STRINGS, ids=["duplicate", "nan", "overflow", "depth"])
def test_preview_final_parser_rejects_invalid_json_before_accessing_persistence(module_name, raw):
    raw = invalid_final_string(raw)
    runner = importlib.import_module(module_name)
    preview = SimpleNamespace(get_task=lambda *_: pytest.fail("Malformed final must not reach persistence"))
    result = runner.assess([{"type": "final_answer", "content": raw}], preview, "synthetic-run")
    assert result["passed"] is False
    assert "STRUCTURED_PROGRAM_RESULT_MISSING" in result["stop_reasons"]


@pytest.mark.parametrize("raw", INVALID_FINAL_STRINGS[:3], ids=["duplicate", "nan", "overflow"])
def test_native_fault_final_parser_rejects_invalid_json(raw):
    from verify_native_faults import root_final
    raw = invalid_final_string(raw)
    assert root_final([{"type": "final_answer", "content": raw}]) is None


def test_native_fault_final_parser_handles_parser_recursion_failure(monkeypatch):
    import verify_native_faults as runner
    def recursion_failure(_):
        raise RecursionError("synthetic parser failure")
    monkeypatch.setattr(runner, "object_from_json", recursion_failure)
    assert runner.root_final([{"type": "final_answer", "content": "{}"}]) is None


@pytest.mark.parametrize("as_object", [False, True])
def test_native_fault_final_parser_preserves_legitimate_string_and_object(as_object):
    from verify_native_faults import root_final
    value = {"status": "ERROR", "data": {"attempts": 2, "published": False}}
    raw = deepcopy(value) if as_object else json.dumps(value)
    assert exact_json_equal(root_final([{"type": "final_answer", "content": raw}]), value)

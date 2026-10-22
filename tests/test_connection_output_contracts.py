"""Optional role ports: strict fixtures only, no model or network calls."""
from copy import deepcopy
import json

import httpx
import pytest

from gov_mcp.decision.adapters import CAPABILITIES
from gov_mcp.decision.connections import Connections, ConnectionError
from gov_mcp.decision.output_contracts import OUTPUT_CONTRACTS
from gov_mcp.decision.workflow import ROOT


SAMPLES = {
    ("query_controller", "plan_query"): {
        "allowed_tools": ["retrieve_registered_evidence", "verify_conditions"], "max_rounds": 3,
        "evidence_ids": ["ev-1"], "unknown_user_fields": ["age_group"]},
    ("retriever", "retrieve_registered_evidence"): {
        "evidence": [{"id": "ev-1", "asset_id": "asset-1", "start": 0, "end": 4,
                      "quote": "模拟原文", "locator": "第1行"}], "missing_evidence_ids": ["ev-2"]},
    ("query_verifier", "verify_conditions"): {
        "condition_results": [{"requirement_id": "req-slip", "result": "unknown", "evidence_ids": ["ev-1"],
                               "missing_fields": ["age_group"]}],
        "evidence_gaps": ["ev-2"], "unknown_user_fields": ["age_group"], "needs_more": True},
    ("query_controller", "compose_answer"): {
        "requirements": [{"requirement_id": "req-slip", "result": "unknown", "evidence_ids": ["ev-1"],
                          "missing_fields": ["age_group"], "reason": "年龄未知，等待补充"},
                         {"requirement_id": "req-old", "result": "paused", "evidence_ids": ["ev-2"],
                          "missing_fields": [], "reason": "来源有变化，等待人工核实"}],
        "unknown_user_fields": ["age_group"], "paused_requirement_ids": ["req-old"],
        "formal_approval": False, "published": False},
    ("update_controller", "plan_update"): {
        "package_ids": ["pkg-1"], "allowed_tools": ["classify_changes", "submit_candidates", "validate_candidates", "finish_run"],
        "requires_professional": True},
    ("extractor", "submit_candidates"): {
        "candidates": [{"kind": "Material", "name": "材料", "quote": "模拟材料原文", "source_segment_ids": ["seg-1"]}]},
    ("update_verifier", "validate_candidates"): {
        "valid_structure": True, "exact_evidence": False, "conflicts": [], "omissions": ["待核实原件"],
        "notes": [], "requires_professional": True},
    ("update_controller", "finish_run"): {
        "package_ids": ["pkg-1"], "status": "awaiting_review", "requires_professional": True,
        "business_rules_published": False},
}


def make_port(tmp_path, handler, transport):
    config = json.loads((ROOT / "config/decision/connections.json").read_text("utf-8"))
    config["inference_enabled"] = True
    if transport == "ollama":
        config["ollama_url"] = "http://127.0.0.1:11434"
        for role, route in config["roles"].items():
            if role != "jev":
                route.update(transport="ollama", model="fixture-qwen", credential_ref=None)
    path = tmp_path / "connections.json"
    path.write_text(json.dumps(config), "utf-8")
    return Connections(path, client=httpx.Client(transport=httpx.MockTransport(handler)),
                       environment={"DEEPSEEK_API_KEY": "fixture-key"})


def reply(transport, request, output):
    body = json.loads(request.content)
    if transport == "deepseek":
        return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(output, ensure_ascii=False)}}], "usage": {"prompt_tokens": 4, "completion_tokens": 3}})
    return httpx.Response(200, json={"model": body["model"], "done": True,
        "message": {"content": json.dumps(output, ensure_ascii=False)}, "eval_count": 3})


def test_every_non_jev_capability_has_exactly_one_candidate_contract():
    assert set(OUTPUT_CONTRACTS) == {(role, tool) for role, tools in CAPABILITIES.items() if role != "jev" for tool in tools}
    assert set(SAMPLES) == set(OUTPUT_CONTRACTS)


@pytest.mark.parametrize("transport", ["deepseek", "ollama"])
@pytest.mark.parametrize("route", SAMPLES)
def test_every_role_uses_its_specific_contract_and_keeps_candidate_unchanged(tmp_path, transport, route):
    calls = []
    def handler(request):
        calls.append(request)
        messages = json.loads(request.content)["messages"]
        schema = OUTPUT_CONTRACTS[route].model_json_schema(by_alias=True)
        assert json.dumps(schema, ensure_ascii=False, separators=(",", ":")) in messages[0]["content"]
        return reply(transport, request, SAMPLES[route])
    port = make_port(tmp_path, handler, transport)
    result = port.request(*route, {"data_class": "synthetic"})
    assert json.dumps(result["output"], ensure_ascii=False) == json.dumps(SAMPLES[route], ensure_ascii=False)
    assert result["status"] == "untrusted_machine_candidate" and len(calls) == 1
    assert port.inference_requests == 1


@pytest.mark.parametrize("transport", ["deepseek", "ollama"])
@pytest.mark.parametrize("route", SAMPLES)
def test_every_role_rejects_unrelated_json_object_without_retry(tmp_path, transport, route):
    calls = []
    def handler(request):
        calls.append(request)
        return reply(transport, request, {"published": 0, "unexpected": "fixture-key"})
    port = make_port(tmp_path, handler, transport)
    code = "DEEPSEEK_OUTPUT_CONTRACT_VIOLATION" if transport == "deepseek" else "QWEN_CALL_FAILED"
    with pytest.raises(ConnectionError, match="^" + code + "$"):
        port.request(*route, {"data_class": "synthetic"})
    assert len(calls) == port.inference_requests == 1


@pytest.mark.parametrize("transport", ["deepseek", "ollama"])
@pytest.mark.parametrize("route,path,value", [
    (("query_controller", "plan_query"), ("max_rounds",), True),
    (("retriever", "retrieve_registered_evidence"), ("evidence", 0, "start"), False),
    (("query_verifier", "verify_conditions"), ("needs_more",), 1),
    (("query_controller", "compose_answer"), ("published",), 0),
    (("update_controller", "plan_update"), ("requires_professional",), 1),
    (("extractor", "submit_candidates"), ("candidates", 0, "quote"), ["模拟材料原文"]),
    (("update_verifier", "validate_candidates"), ("exact_evidence",), 0),
    (("update_controller", "finish_run"), ("business_rules_published",), 0),
])
def test_contracts_reject_wrong_scalar_and_nested_types(tmp_path, transport, route, path, value):
    output = deepcopy(SAMPLES[route])
    node = output
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    port = make_port(tmp_path, lambda request: reply(transport, request, output), transport)
    with pytest.raises(ConnectionError):
        port.request(*route, {"data_class": "synthetic"})


@pytest.mark.parametrize("raw", [
    '{"model":"fixture-qwen","done":false,"done":true,"message":{"content":"{\\"candidates\\":[]}"}}',
    '{"model":"fixture-qwen","done":true,"message":{"content":"{\\"candidates\\":[],\\"candidates\\":[]}"}}',
    '{"model":"fixture-qwen","done":true,"message":{"content":"{\\"candidates\\":[]}"},"extra":NaN}',
    '{"model":"fixture-qwen","done":true,"message":{"content":"{\\"candidates\\":[],\\"extra\\":NaN}"}}',
])
def test_qwen_strictly_parses_outer_and_inner_json(tmp_path, raw):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=raw)
    port = make_port(tmp_path, handler, "ollama")
    with pytest.raises(ConnectionError, match="^QWEN_CALL_FAILED$"):
        port.request("extractor", "submit_candidates", {"data_class": "synthetic"})
    assert len(calls) == 1


@pytest.mark.parametrize("event", [
    '{"type":"error","type":"final_answer","depth":0}',
    '{"type":"final_answer","depth":0,"extra":NaN}',
    '{"type":"final_answer","depth":0,"extra":1e999}',
    '{"type":[],"depth":0}',
    '[]',
])
def test_native_sse_rejects_invalid_json_then_confirms_stop(tmp_path, event):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/user/signin":
            return httpx.Response(200, json={"data": {"session": {"access_token": "fixture-session"}}})
        if request.url.path == "/conversation/create":
            return httpx.Response(200, json={"data": {"conversation_id": 41}})
        if request.url.path == "/agent/stop/41":
            return httpx.Response(200, json={"stopped": True})
        return httpx.Response(200, text="data: " + event + "\n\n", headers={"content-type": "text/event-stream"})
    port = make_port(tmp_path, handler, "ollama")
    with pytest.raises(ConnectionError, match="^NATIVE_CALL_FAILED$"):
        port.request_nexent("query_controller", {"data_class": "synthetic"},
            binding={"agent_id": 16, "model_id": 1, "version_no": 1}, credentials={"email": "fixture", "password": "fixture"})
    assert paths == ["/user/signin", "/conversation/create", "/agent/run", "/agent/stop/41"]
    assert port.inference_requests == 1

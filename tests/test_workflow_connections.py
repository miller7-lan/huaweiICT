"""Connection contracts without real model inference or paid API requests."""
import json
from pathlib import Path
import sys

import httpx
import pytest
from starlette.testclient import TestClient

from gov_mcp.decision.connections import Connections, ConnectionError
from gov_mcp.decision.workflow import Workflow, WorkflowError, ROOT
from gov_mcp.decision.server import create_app

FACTS = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}


def configured(tmp_path, *, enabled=False, key_ref=None, handler=None, environment=None, jev_factory=None):
    data = json.loads((ROOT/"config/decision/connections.json").read_text("utf-8"))
    data["inference_enabled"] = enabled
    data["roles"]["jev"]["credential_ref"] = key_ref
    path = tmp_path/"connections.json"
    path.write_text(json.dumps(data))
    args = {"environment": environment or {}}
    if handler is not None: args["client"] = httpx.Client(transport=httpx.MockTransport(handler))
    if jev_factory is not None: args["jev_factory"] = jev_factory
    return Connections(path, **args)


@pytest.mark.parametrize("role,tool", [("query_controller", "plan_query"), ("retriever", "retrieve_registered_evidence"),
    ("query_verifier", "verify_conditions"), ("update_controller", "plan_update"), ("extractor", "submit_candidates"),
    ("update_verifier", "validate_candidates"), ("jev", "classify_changes")])
def test_all_routes_prepared_and_disabled_gate_sends_nothing(tmp_path, role, tool):
    calls = []
    port = configured(tmp_path, handler=lambda req: calls.append(req))
    assert port.prepare(role, tool, {"data_class": "synthetic"})["status"] == "ready_for_final_api_activation"
    with pytest.raises(ConnectionError, match="WAITING_FINAL_API_ACTIVATION"):
        port.request(role, tool, {"data_class": "synthetic"})
    assert not calls


def test_probe_reads_only_local_metadata_never_jev_or_generation(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == "/api/tags": return httpx.Response(200, json={"models": [{"name": "gov-qwen3-8b:ctx8192-v1"}]})
        return httpx.Response(200, json={"status": "ready"})
    port = configured(tmp_path, handler=handler)
    result = port.probe()
    assert len(calls) == 3 and all(r.method == "GET" for r in calls)
    assert not result["jev_network_request_sent"] and result["inference_calls"] == 0
    assert all(r.url.host == "127.0.0.1" for r in calls)
    assert result["roles"][0]["model_available"]


def test_qwen_request_shape_and_untrusted_output_after_explicit_gate(tmp_path):
    def handler(req):
        body = json.loads(req.content)
        assert req.url.path == "/api/chat" and body["keep_alive"] == 0 and body["stream"] is False
        assert body["think"] is False and body["options"]["num_ctx"] == 8192
        assert not req.headers.get("Authorization")
        return httpx.Response(200, json={"model": body["model"], "done": True, "message": {"content": '{"candidate_ids": ["req-slip"]}'}, "eval_count": 8})
    port = configured(tmp_path, enabled=True, handler=handler)
    result = port.request("extractor", "submit_candidates", {"data_class": "synthetic", "text": "模拟资料"})
    assert result["status"] == "untrusted_machine_candidate" and result["usage"]["output_tokens"] == 8


@pytest.mark.parametrize("response", [{"model": "wrong", "done": True},
    {"model": "gov-qwen3-4b:ctx8192-v1", "done": False},
    {"model": "gov-qwen3-4b:ctx8192-v1", "done": True, "message": {"content": "不是 JSON"}}])
def test_bad_model_outputs_fail_without_leaking_bodies(tmp_path, response):
    port = configured(tmp_path, enabled=True, handler=lambda req: httpx.Response(200, json=response))
    with pytest.raises(ConnectionError, match="^QWEN_CALL_FAILED$"):
        port.request("extractor", "submit_candidates", {"data_class": "synthetic"})


def test_jev_credentials_last_and_response_contract_checked(tmp_path):
    called = []
    class Jev:
        def __init__(self, api_key): called.append(api_key)
        def evaluate(self, state, questions, model):
            return {"model": model, "answers": {"support": {"type": "noul", "noul": .9}}, "usage": {"input_tokens": 4, "output_tokens": 1}}
    data = {"data_class": "synthetic", "state": {"quote": "模拟"}, "questions": {"support": {"type": "noul", "criterion": "支持"}}}
    port = configured(tmp_path, enabled=True, jev_factory=Jev)
    with pytest.raises(ConnectionError, match="WAITING_JEV_CREDENTIAL"): port.request("jev", "check_support", data)
    assert not called
    port = configured(tmp_path, enabled=True, key_ref="env:TYPESAFE_API_KEY", environment={"TYPESAFE_API_KEY": "test-only-key"}, jev_factory=Jev)
    assert port.request("jev", "check_support", data)["answers"]["support"]["noul"] == .9
    assert called == ["test-only-key"]
    assert "test-only-key" not in json.dumps(port.describe())


def test_native_port_gate_is_before_login_and_binding_check(tmp_path):
    calls = []
    port = configured(tmp_path, handler=lambda req: calls.append(req))
    with pytest.raises(ConnectionError, match="WAITING_FINAL_API_ACTIVATION"):
        port.request_nexent("query_controller", {"data_class": "synthetic"}, binding={}, credentials={})
    assert not calls
    port = configured(tmp_path, enabled=True, handler=lambda req: calls.append(req))
    with pytest.raises(ConnectionError, match="WAITING_NEXENT_MODEL_BINDING"):
        port.request_nexent("query_controller", {"data_class": "synthetic"}, binding={}, credentials={})
    assert not calls


def test_native_sse_transport_with_fixture_http_only(tmp_path):
    calls = []
    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/user/signin": return httpx.Response(200, json={"data": {"session": {"access_token": "fixture-session"}}})
        assert req.headers["authorization"] == "Bearer fixture-session"
        if req.url.path == "/conversation/create": return httpx.Response(200, json={"data": {"conversation_id": 41}})
        body = json.loads(req.content)
        assert body["agent_id"] == 16 and body["model_id"] == 1 and body["version_no"] == 1
        return httpx.Response(200, text='data: {"type":"final_answer","depth":0,"content":"fixture"}\n\n', headers={"content-type": "text/event-stream"})
    port = configured(tmp_path, enabled=True, handler=handler)
    result = port.request_nexent("query_controller", {"data_class": "synthetic", "facts": FACTS},
        binding={"agent_id": 16, "model_id": 1, "version_no": 1}, credentials={"email": "fixture", "password": "fixture"})
    assert calls == ["/user/signin", "/conversation/create", "/agent/run"]
    assert result["status"] == "untrusted_native_output_requires_tool_trace_validation"


def test_staged_workflow_order_idempotency_and_live_gate(tmp_path):
    w = Workflow(tmp_path)
    opened = w.open_query(FACTS, "branch-adult-self")
    key = opened["task_id"]
    with pytest.raises(WorkflowError, match="QUERY_NOT_VALIDATED"): w.finalize_query(key)
    first = w.retrieve_query(key)
    assert w.retrieve_query(key) == first
    assert w.verify_query(key)["needs_more"] is True
    w.retrieve_query(key)
    assert w.verify_query(key)["status"] == "can_finalize"
    before = w.finalize_query(key)
    assert before["counts"]["confirmed"] == 4 and before["retrieval_rounds"] == 2
    w.configure_demo(actor="demo-professional")
    w.set_profile("changed", actor="demo-professional")
    w.heartbeat(force=True)
    after = w.finalize_query(key)
    assert after["counts"]["paused"] == 2
    assert w.history(key)["original_answer"] == before


def test_staged_mcp_native_child_boundary(tmp_path):
    w = Workflow(tmp_path)
    with TestClient(create_app(w, "professional", "service", start_worker=False), base_url="http://127.0.0.1:8780") as client:
        def call(name, params):
            r = client.post("/mcp/", headers={"Authorization": "Bearer service", "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": params}})
            result = r.json()["result"]
            assert not result.get("isError"), result
            return result.get("structuredContent") or json.loads(result["content"][0]["text"])
        task = call("govkb_open_query", {"facts": FACTS, "branch_id": "branch-adult-self"})["task_id"]
        for _ in range(3):
            call("govkb_retrieve_query", {"task_id": task})
            checked = call("govkb_verify_query", {"task_id": task})
            if not checked["needs_more"]: break
        assert call("govkb_query_state", {"task_id": task})["rounds"] == 2
        assert call("govkb_finalize_query", {"task_id": task})["counts"]["confirmed"] == 4
        assert client.get("/api/connections").status_code == 403


def test_native_draft_definitions_have_empty_models_and_separate_child_tools():
    sys.path.insert(0, str(ROOT/"scripts"))
    from provision_agent_workflow import make_bundle
    query = make_bundle("query_controller")
    update = make_bundle("update_controller")
    all_agents = [*query["agent_info"].values(), *update["agent_info"].values()]
    assert len(all_agents) == 6
    assert all(a["model_ids"] == [] and a["model_names"] == [] and a["enabled"] is False for a in all_agents)
    assert query["agent_info"]["1"]["managed_agents"] == [2, 3]
    for a in all_agents:
        assert not any("publish" in t["name"] or "rollback" in t["name"] for t in a["tools"])

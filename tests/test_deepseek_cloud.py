"""Cloud migration contracts; all HTTP responses are local test fixtures."""
from copy import deepcopy
import json

import httpx
import pytest

from gov_mcp.deepseek import chat, PROFILES, CloudError, ROOT
from gov_mcp.decision.connections import Connections, ConnectionError
from gov_mcp.knowledge.engine import Engine
from gov_mcp.knowledge.store import KBError
from configure_deepseek import bind_drafts


def port(tmp_path, handler, *, enabled=True, environment=None):
    value = json.loads((ROOT/"config/decision/connections.json").read_text("utf-8"))
    value["inference_enabled"] = enabled
    path = tmp_path/"connections.json"
    path.write_text(json.dumps(value))
    return Connections(path, client=httpx.Client(transport=httpx.MockTransport(handler)), environment=environment or {})


def fixture_output(model="deepseek-v4.1-flash-1017"):
    return {"model": model, "choices": [{"finish_reason": "stop", "message": {"content": '{"candidate_ids":["req-slip"]}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8}}


def test_all_six_cloud_roles_preserve_actual_model_and_never_send_ollama_fields(tmp_path):
    seen = []
    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        assert body["thinking"] == {"type": "disabled"} and body["response_format"] == {"type": "json_object"}
        assert not {"keep_alive", "options", "think", "reasoning_effort"} & set(body)
        return httpx.Response(200, json=fixture_output())
    conn = port(tmp_path, handler, environment={"DEEPSEEK_API_KEY": "fixture-secret"})
    tools = {"query_controller": "plan_query", "retriever": "retrieve_registered_evidence", "query_verifier": "verify_conditions",
             "update_controller": "plan_update", "extractor": "submit_candidates", "update_verifier": "validate_candidates"}
    for role, tool in tools.items():
        output = conn.request(role, tool, {"data_class": "synthetic", "text": "模拟资料"})
        assert output["requested_model"] == "deepseek-flash" and output["model"] == "deepseek-v4.1-flash-1017"
        assert output["status"] == "untrusted_machine_candidate"
    assert len(seen) == 6 and "fixture-secret" not in json.dumps(conn.describe())


def test_disabled_missing_key_private_data_and_unauthorized_tool_never_send(tmp_path):
    def forbidden(request): pytest.fail("unexpected network request")
    conn = port(tmp_path, forbidden, enabled=False)
    with pytest.raises(ConnectionError, match="WAITING_FINAL_API_ACTIVATION"):
        conn.request("extractor", "submit_candidates", {"data_class": "synthetic"})
    conn = port(tmp_path, forbidden)
    with pytest.raises(ConnectionError, match="WAITING_DEEPSEEK_CREDENTIAL"):
        conn.request("extractor", "submit_candidates", {"data_class": "synthetic"})
    with pytest.raises(ConnectionError, match="DATA_CLASS_BLOCKED"):
        conn.request("extractor", "submit_candidates", {"data_class": "private"})
    with pytest.raises(ConnectionError, match="ROLE_TOOL_NOT_ALLOWED"):
        conn.request("extractor", "publish", {"data_class": "synthetic"})
    assert conn.inference_requests == 0


@pytest.mark.parametrize("status,code", [(401,"DEEPSEEK_AUTHENTICATION_FAILED"),(402,"DEEPSEEK_PAYMENT_REQUIRED"),
    (403,"DEEPSEEK_ACCESS_DENIED"),(429,"DEEPSEEK_RATE_LIMITED"),(500,"DEEPSEEK_HTTP_ERROR")])
def test_cloud_errors_do_not_retry_fallback_or_leak(tmp_path, status, code):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="fixture-secret upstream private message")
    conn = port(tmp_path, handler, environment={"DEEPSEEK_API_KEY": "fixture-secret"})
    with pytest.raises(ConnectionError, match="^"+code+"$"):
        conn.request("extractor", "submit_candidates", {"data_class": "synthetic"})
    assert len(seen) == 1


@pytest.mark.parametrize("mutation", ["truncated", "invalid_json", "unexpected_model", "invalid_usage", "tool_call"])
def test_cloud_output_is_validated(mutation):
    value = fixture_output()
    if mutation == "truncated": value["choices"][0]["finish_reason"] = "length"
    if mutation == "invalid_json": value["choices"][0]["message"]["content"] = "[]"
    if mutation == "unexpected_model": value["model"] = "other-model"
    if mutation == "invalid_usage": value["usage"]["prompt_tokens"] = -1
    if mutation == "tool_call": value["choices"][0]["message"]["tool_calls"] = [{"name": "publish"}]
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)))
    with pytest.raises(CloudError): chat(PROFILES["official"], [], client=client, environment={"DEEPSEEK_API_KEY":"fixture-secret"})


def test_cloud_probe_reads_only_local_nexent_metadata(tmp_path):
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"status":"ready"})
    conn = port(tmp_path, handler)
    result = conn.probe()
    assert set(result["services"]) == {"nexent_config", "nexent_runtime"}
    assert all(url.startswith("http://127.0.0.1:") for url in seen) and len(seen) == 2
    assert conn.inference_requests == 0 and all(not r["credential_configured"] for r in result["roles"] if r["role"] != "jev")


def test_redirected_origin_is_rejected_before_credentials_or_network():
    config = {**PROFILES["official"], "base_url":"https://example.com/v1"}
    with pytest.raises(CloudError, match="INVALID_DEEPSEEK_PROFILE"):
        chat(config, [], environment={"DEEPSEEK_API_KEY":"fixture-secret"})


def test_new_dense_indexing_waits_for_embedding_instead_of_falling_back(monkeypatch):
    engine = object.__new__(Engine)
    engine.config = json.loads((ROOT/"config/knowledge/heartbeat.json").read_text("utf-8"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("local/cloud embedding unexpectedly called"))
    with pytest.raises(KBError, match="EMBEDDING_PROVIDER_NOT_CONFIGURED"): engine.embed(["模拟资料"])


def test_active_native_agent_is_not_rebound():
    class API:
        def __init__(self): self.agent_writes=[]
        def call(self, method, path, body=None):
            if path == "/model/list": return {"data":[{"model_id":9,"display_name":"deepseek-flash", "model_name":"deepseek-flash", "base_url":PROFILES["official"]["base_url"]}]}
            if path.startswith("/model/update"): return {}
            if path == "/agent/search_info": return {"name":"govflow_extractor", "enabled":True}
            if path.endswith("/versions"): return {"total":1}
            self.agent_writes.append(path)
    api=API()
    with pytest.raises(CloudError, match="REFUSE_TO_OVERWRITE_ACTIVE_AGENT"):
        bind_drafts(api, PROFILES["official"], "fixture-secret", {"agents":{"extractor":17}})
    assert not api.agent_writes

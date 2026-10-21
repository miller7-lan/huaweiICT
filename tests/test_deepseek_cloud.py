"""Cloud migration contracts; all HTTP responses are local test fixtures."""
from copy import deepcopy
import json

import httpx
import pytest

from gov_mcp.deepseek import chat, compact_json, PROFILES, CloudError, ROOT
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


def test_cloud_default_honors_operator_proxy_without_following_redirects(monkeypatch):
    original = httpx.Client
    seen = []
    def client_factory(**kwargs):
        seen.append(kwargs)
        def handle(request):
            body = json.loads(request.content)
            assert body["temperature"] == 0.0
            assert body["thinking"] == {"type": "disabled"}
            return httpx.Response(200, json=fixture_output())
        return original(transport=httpx.MockTransport(handle), trust_env=False)
    monkeypatch.setattr(httpx, "Client", client_factory)
    result = chat(PROFILES["official"], [], environment={"DEEPSEEK_API_KEY": "fixture-secret"})
    assert result["output"] == {"candidate_ids": ["req-slip"]}
    assert seen[0]["trust_env"] is True and seen[0]["follow_redirects"] is False


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


@pytest.mark.parametrize("available", [False, True])
def test_native_binding_requires_verified_model_and_preserves_existing_settings(available):
    class API:
        def __init__(self): self.bound = False; self.writes = []
        def call(self, method, path, body=None):
            if path == "/agent/search_info":
                return {"name": "govflow_extractor", "enabled": False, "model_ids": [9] if self.bound else []}
            if path.endswith("/versions"): return {"total": 0}
            if path == "/model/list":
                return {"data": [{"model_id": 9, "display_name": "deepseek-flash", "model_name": "deepseek-flash",
                                  "base_url": PROFILES["official"]["base_url"],
                                  "connect_status": "available" if available else "not_detected"}]}
            if path == "/agent/update":
                assert body["model_params_override"]["9"]["temperature"] == 0.0
                assert body["model_params_override"]["9"]["extra_params"]["__custom__"]["thinking"] == {"type": "disabled"}
                self.bound = True; self.writes.append(path); return {}
            pytest.fail("Unexpected model mutation or inference: " + path)
    api = API()
    saved = {"agents": {"extractor": 17}}
    if available:
        assert bind_drafts(api, PROFILES["official"], "fixture-secret", saved)["extractor"]["model_id"] == 9
        assert api.writes == ["/agent/update"]
    else:
        with pytest.raises(CloudError, match="NATIVE_MODEL_HEALTHCHECK_REQUIRED"):
            bind_drafts(api, PROFILES["official"], "fixture-secret", saved)
        assert not api.writes


def test_compact_json_preserves_types_order_and_complete_evidence():
    value = {"quote": "未成年人不适用；5个工作日，须核对原件。", "ids": ["ev-1", "ev-2"], "unknown": None, "flag": False}
    packed = compact_json(value)
    assert json.loads(packed) == value
    assert list(json.loads(packed)) == list(value)
    assert len(packed.encode()) < len(json.dumps(value, ensure_ascii=False).encode())
    assert value["quote"] in packed
    with pytest.raises(ValueError): compact_json({"value": float("nan")})


def test_role_payload_uses_compact_json_without_removing_evidence(tmp_path):
    payload = {"data_class": "synthetic", "evidence": [{"id": "ev-1", "quote": "未成年人不适用，限5个工作日。"}], "unknown": None}
    seen = []
    def handle(request):
        seen.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json=fixture_output())
    conn = port(tmp_path, handle, environment={"DEEPSEEK_API_KEY": "fixture-secret"})
    conn.request("query_verifier", "verify_conditions", payload)
    assert seen == [compact_json(payload)] and json.loads(seen[0]) == payload


def test_cache_usage_is_retained_without_treating_missing_details_as_zero():
    value = fixture_output()
    def call():
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value)))
        return chat(PROFILES["official"], [], client=client, environment={"DEEPSEEK_API_KEY": "fixture-secret"})
    assert call()["usage"] == {"input_tokens": 20, "output_tokens": 8}
    value["usage"].update(prompt_cache_hit_tokens=12, prompt_cache_miss_tokens=8)
    assert call()["usage"] == {"input_tokens": 20, "output_tokens": 8, "input_cache_hit_tokens": 12, "input_cache_miss_tokens": 8}


@pytest.mark.parametrize("details", [
    {"prompt_cache_hit_tokens": 1},
    {"prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": 19},
    {"prompt_cache_hit_tokens": -1, "prompt_cache_miss_tokens": 21},
    {"prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 11},
    {"prompt_cache_hit_tokens": "12", "prompt_cache_miss_tokens": 8},
])
def test_invalid_cache_usage_is_rejected(details):
    value = fixture_output()
    value["usage"].update(details)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value)))
    with pytest.raises(CloudError, match="INVALID_DEEPSEEK_USAGE"):
        chat(PROFILES["official"], [], client=client, environment={"DEEPSEEK_API_KEY": "fixture-secret"})

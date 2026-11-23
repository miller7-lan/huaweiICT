"""Wiring of the flow's draft assistant reuses the existing scripts without changing them."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import frozen_flow_native as native
import provision_frozen_qa_native as original
import run_frozen_qa_native as original_run


def test_private_copy_carries_the_flow_settings_and_leaves_the_original_alone():
    before = (original.SERVICE, original.PORT, set(original.TOOLS), original.NAME, original.PROMPT, original.RUNTIME)
    module = native.provisioner()
    assert module is not original and (module.SERVICE, module.PORT, module.NAME) == ("govkb_frozen_flow", 8787, native.NAME)
    draft = module.body(7, {"govkb_flow_open": 11, "govkb_flow_submit": 12}, 3)
    assert draft["enabled"] is False and draft["max_steps"] == native.MAX_STEPS and draft["name"] == native.NAME
    assert draft["enabled_tool_ids"] == [11, 12] and draft["duty_prompt"] == native.PROMPT
    assert draft["related_agent_ids"] == [] and draft["enabled_skill_ids"] == []
    assert (original.SERVICE, original.PORT, set(original.TOOLS), original.NAME, original.PROMPT, original.RUNTIME) == before
    assert original.body(7, {"govkb_frozen_retrieve": 1, "govkb_frozen_verify": 2}, 3)["max_steps"] == 6


def test_runner_uses_the_flow_runtime_only():
    module = native.runner()
    assert module.RUNTIME == native.RUNTIME and module.OUTPUT == native.OUTPUT
    assert original_run.RUNTIME != native.RUNTIME
    # The flow gets more model starts; the earlier drafts keep their own limit.
    assert module.MAX_MODEL_STARTS == native.MAX_MODEL_STARTS == 10 and original_run.MAX_MODEL_STARTS == 8


def test_a_native_run_is_refused_when_the_service_uses_another_prompt_profile():
    import pytest
    health = {"service": "govkb_frozen_flow", "status": "UP", "rules": "light", "corpus_sha256": "c" * 64}
    assert native.check_service_rules(health, "light")["rules"] == "light"
    with pytest.raises(RuntimeError, match="SERVICE_RULES_MISMATCH"):
        native.check_service_rules(health, "strict")
    with pytest.raises(RuntimeError, match="FLOW_SERVICE_NOT_RUNNING"):
        native.check_service_rules({"service": "govkb_frozen_qa", "status": "UP", "rules": "strict"}, "strict")


def test_prompt_names_both_tools_and_the_plan_registers_nothing():
    assert all(tool in native.PROMPT for tool in native.TOOLS)
    assert "Never call govkb_flow_open twice" in native.PROMPT
    assert "\nimport json\nraw = govkb_flow_open(question=...)" in native.PROMPT
    assert "copy the complete user message verbatim" in native.PROMPT
    assert "answer=''" in native.PROMPT
    assert "json.loads(raw_submit)" in native.PROMPT
    assert 'final_answer(result["display_text"])' in native.PROMPT
    plan = native.plan()
    assert plan["enabled"] is False and plan["published"] is False and plan["tools"] == sorted(native.TOOLS)


def test_local_mcp_probe_ignores_the_environment_proxy(monkeypatch):
    module = native.provisioner()
    health = {"service": module.SERVICE, "status": "UP"}
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)

    def get(url, *, timeout, trust_env):
        assert url == "http://127.0.0.1:8787/health" and not trust_env
        return SimpleNamespace(json=lambda: health)

    @asynccontextmanager
    async def client(*, trust_env, headers):
        assert not trust_env and headers["Authorization"] == "Bearer test-token"
        yield

    @asynccontextmanager
    async def stream(url, *, headers, httpx_client_factory):
        assert url == "http://127.0.0.1:8787/sse" and headers["Authorization"] == "Bearer test-token"
        async with httpx_client_factory(headers=headers):
            yield ()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in module.TOOLS])

    monkeypatch.setattr(module.httpx, "get", get)
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    monkeypatch.setattr(module, "sse_client", stream)
    monkeypatch.setattr(module, "ClientSession", Session)
    assert module.check_service("test-token") == health


@pytest.mark.parametrize("published", [False, True])
def test_refreshes_only_the_unpublished_v3_draft(monkeypatch, published):
    module = native.provisioner()
    monkeypatch.setattr(module, "read", lambda _: {"group_id": 7})
    monkeypatch.setattr(module, "other_signatures", lambda *_: {})
    model = {"model_id": 3, "model_name": "deepseek-flash", "base_url": "https://api.deepseek.com/v1",
             "connect_status": "available"}
    tools = [{"name": name, "tool_id": index, "source": "mcp", "usage": module.SERVICE, "is_available": True}
             for index, name in enumerate(sorted(module.TOOLS), 1)]
    draft = {"agent_id": 4, "name": module.NAME, "tools": tools, "enabled": False, "model_ids": [3],
             "duty_prompt": "Earlier prompt", "constraint_prompt": "Earlier constraints"}
    updates = []

    def call(method, path, body=None):
        if path == "/agent/update":
            assert method == "POST"
            updates.append(body)
            draft.update(body)
        return {
            "/model/list": {"data": [model]},
            "/mcp/list": {"remote_mcp_server_list": [
                {"mcp_name": module.SERVICE, "server_url": f"http://host.docker.internal:{module.PORT}/sse"}]},
            "/tool/list": tools,
            "/agent/4/versions": {"total": int(published)},
            "/agent/search_info": dict(draft),
        }.get(path)

    saved = {"agents": {"assistant": 4}, "model_id": 3, "published": False}
    if published:
        with pytest.raises(RuntimeError, match="REFUSE_TO_REFRESH_ACTIVE_DRAFT"):
            module.provision(SimpleNamespace(call=call), saved, "test-token")
        assert not updates
    else:
        assert module.provision(SimpleNamespace(call=call), saved, "test-token")["configured"]
        assert len(updates) == 1 and updates[0]["duty_prompt"] == module.PROMPT

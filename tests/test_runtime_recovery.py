"""Verify recovery ownership and the narrow native connection error boundary."""

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import runtime_sandbox
from runtime_error_overlay import BASE_SHA256, ROOT, mount_overlay, patched_source


def sandbox(status="exited", image=runtime_sandbox.IMAGE, owner="runtime", restart="no"):
    return {"Config": {"Image": image, "Labels": {"com.nexent.sandbox": owner,
            "io.nexent.sandbox.variant": "lightweight"}}, "State": {"Status": status},
            "HostConfig": {"RestartPolicy": {"Name": restart}}}


def test_recovery_preserves_existing_container_and_adds_restart_policy(monkeypatch):
    calls = []
    state = sandbox()
    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps([state]))
        if "start" in args:
            state["State"]["Status"] = "running"
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime_sandbox.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(runtime_sandbox.subprocess, "run", run)
    assert runtime_sandbox.recover_sandbox() == "RUNNING"
    assert [args[1] for args in calls] == ["container", "update", "start", "container"]
    assert not any("rm" in args or "create" in args for args in calls)


@pytest.mark.parametrize("field,value", [("image", "other:v2.7.0"), ("owner", "other")])
def test_unknown_sandbox_identity_is_never_mutated(monkeypatch, field, value):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps([sandbox(**{field: value})]))
    monkeypatch.setattr(runtime_sandbox.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(runtime_sandbox.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="identity"):
        runtime_sandbox.recover_sandbox()
    assert len(calls) == 1


@pytest.mark.parametrize("error,ready", [("No such container: nexent-runtime-sandbox", True),
                                        ("Cannot connect to Docker daemon", False)])
def test_missing_sandbox_differs_from_unavailable_engine(monkeypatch, error, ready):
    monkeypatch.setattr(runtime_sandbox.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(runtime_sandbox.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stderr=error))
    assert runtime_sandbox.sandbox_ready() is ready


def error_mapper():
    original = (ROOT / "vendor/nexent/sdk/nexent/core/agents/run_agent.py").read_bytes()
    assert hashlib.sha256(original).hexdigest() == BASE_SHA256
    tree = ast.parse(patched_source(original))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("_blank_mcp_connection_failure", "_agent_run_error_message")]
    namespace = {"httpx": httpx, "json": json, "AgentRunInfo": object,
                 "is_mcp_connection_timeout_error": lambda _: False,
                 "is_mcp_timeout_error": lambda _: False}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "overlay", "exec"), namespace)
    return namespace["_agent_run_error_message"]


def test_nested_connection_exception_produces_native_stop_contract():
    error = ExceptionGroup("TaskGroup", [ExceptionGroup("nested", [httpx.ConnectError("private-address")])])
    answer = error_mapper()(object(), [{"url": "http://gov-blank-mcp:8765/sse"}], error)
    value = json.loads(answer)
    assert value == {"status": "ERROR", "items": [], "citations": [], "knowledge_version": None,
                     "message_code": "CONNECTION_TIMEOUT", "data": {"attempts": 1, "phase": "mcp_connection"}}
    assert "private-address" not in answer


@pytest.mark.parametrize("error", [ValueError("bad configuration"), httpx.ReadTimeout("tool read"),
    ExceptionGroup("mixed", [httpx.ConnectError("connection"), ValueError("wrong config")]),
    httpx.HTTPStatusError("unauthorized", request=httpx.Request("GET", "http://example.test"),
                          response=httpx.Response(401))])
def test_other_failures_keep_upstream_mapping(error):
    assert error_mapper()(object(), ["http://gov-blank-mcp:8765/sse"], error).startswith("Run Agent Error:")


def test_connection_mapping_does_not_apply_to_other_mcp_servers():
    assert error_mapper()(object(), ["http://another-mcp/sse"], httpx.ConnectError("failed")).startswith("Run Agent Error:")


def test_overlay_refuses_source_drift_and_mount_is_idempotent():
    with pytest.raises(ValueError, match="differs"):
        patched_source(b"changed source")
    source = Path("C:/project/.runtime/overlays/run_agent.py")
    original = "services:\n  nexent-runtime:\n    volumes:\n      - data:/data\n  nexent-web:\n    image: web\n"
    mounted = mount_overlay(original, source)
    assert mount_overlay(mounted, source) == mounted
    assert mounted.endswith("  nexent-web:\n    image: web\n")
    assert "data:/data" in mounted and ":ro" in mounted


def test_container_storage_lock_releases_when_agent_raises(monkeypatch):
    import container_storage_lock as storage
    closed = []
    process = SimpleNamespace(poll=lambda: None, returncode=0,
                              communicate=lambda **kw: closed.append(kw))
    monkeypatch.setattr(storage.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(storage.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(storage.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="Agent failed"):
        with storage.container_storage_lock():
            raise RuntimeError("Agent failed")
    assert closed == [{"input": b"\n", "timeout": 10}]

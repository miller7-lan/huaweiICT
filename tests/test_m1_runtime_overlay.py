import ast
import json
from pathlib import Path

import httpx
import pytest

from prepare_m1_runtime import connection_overlay, sandbox_overlay


def mapper():
    data = connection_overlay(Path("vendor/nexent/sdk/nexent/core/agents/run_agent.py").read_bytes())
    functions = [node for node in ast.parse(data.decode()).body if isinstance(node, ast.FunctionDef)
                 and node.name in ("_blank_mcp_connection_failure", "_agent_run_error_message")]
    namespace = {"httpx": httpx, "json": json, "AgentRunInfo": object,
                 "is_mcp_connection_timeout_error": lambda _: False, "is_mcp_timeout_error": lambda _: False}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "govx-overlay", "exec"), namespace)
    return namespace["_agent_run_error_message"]


def test_govx_connection_failure_has_terminal_contract_without_private_exception():
    error = ExceptionGroup("task", [ExceptionGroup("nested", [httpx.ConnectError("private-endpoint")])])
    text = mapper()(object(), [{"url": "http://govx-blank-mcp:8766/sse"}], error)
    assert json.loads(text) == {"status": "ERROR", "items": [], "citations": [], "knowledge_version": None,
        "message_code": "CONNECTION_TIMEOUT", "data": {"attempts": 1, "phase": "mcp_connection"}}
    assert "private-endpoint" not in text


@pytest.mark.parametrize("urls", [["http://gov-blank-mcp:8765/sse"], ["http://other-mcp/sse"],
    ["http://govx-blank-mcp:8766/sse", "http://other-mcp/sse"], []])
def test_private_overlay_does_not_reclassify_other_servers(urls):
    assert mapper()(object(), urls, httpx.ConnectError("error")).startswith("Run Agent Error:")


@pytest.mark.parametrize("error", [httpx.ReadTimeout("read"), ValueError("configuration"),
    ExceptionGroup("mixed", [httpx.ConnectError("connection"), ValueError("configuration")])])
def test_nonconnection_and_mixed_errors_are_not_reclassified(error):
    assert mapper()(object(), ["http://govx-blank-mcp:8766/sse"], error).startswith("Run Agent Error:")


def test_sandbox_private_overlay_changes_only_owned_names_and_callback_host():
    original = Path("vendor/nexent/sdk/nexent/core/agents/sandbox.py").read_bytes()
    patched = sandbox_overlay(original)
    reverted = patched.replace(b'SANDBOX_CONTAINER_NAME = "nexent-m1-runtime-sandbox"',
                               b'SANDBOX_CONTAINER_NAME = "nexent-runtime-sandbox"').replace(
        b'SANDBOX_SESSION_CONTAINER_PREFIX = "nexent-m1-runtime-sandbox-session"',
        b'SANDBOX_SESSION_CONTAINER_PREFIX = "nexent-runtime-sandbox-session"').replace(
        b'return "nexent-m1-runtime" if _is_containerized_runtime() else "host.docker.internal"',
        b'return "nexent-runtime" if _is_containerized_runtime() else "host.docker.internal"')
    assert reverted == original
    with pytest.raises(ValueError):
        sandbox_overlay(b"unknown SDK source")
    with pytest.raises(ValueError):
        connection_overlay(b"unknown SDK source")

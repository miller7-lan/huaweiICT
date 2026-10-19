"""Verify real SSE tool discovery and terminal responses without business fixtures."""

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client

from gov_mcp.core import BlankStore
from gov_mcp.server import create_app


async def verify(port, token):
    records = []
    def local_client(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    async with sse_client(f"http://127.0.0.1:{port}/sse", headers={"Authorization": f"Bearer {token}", "Host": "gov-blank-mcp:8765"}, httpx_client_factory=local_client) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {t.name for t in listed.tools} == {"get_environment_status", "search_evidence", "check_case_prerequisites"}
            for t in listed.tools:
                assert set(t.inputSchema["properties"]) == {"tenant_id", "user_id"}
            cases = [("get_environment_status", "citizen", "OK", "ENVIRONMENT_READY"),
                     ("search_evidence", "citizen", "EMPTY", "NO_KNOWLEDGE"),
                     ("check_case_prerequisites", "staff", "BLOCKED", "MISSING_CASE_AND_MATERIALS"),
                     ("check_case_prerequisites", "citizen", "BLOCKED", "FORBIDDEN"),
                     ("get_environment_status", "unmapped", "BLOCKED", "ROLE_NOT_CONFIGURED")]
            for tool, user, status, code in cases:
                started = time.perf_counter()
                response = await session.call_tool(tool, {"tenant_id": "transport-test", "user_id": user})
                assert not response.isError
                value = response.structuredContent or json.loads(response.content[0].text)
                assert (value["status"], value["message_code"]) == (status, code)
                assert value["items"] == value["citations"] == [] and value["knowledge_version"] is None
                records.append({"tool": tool, "result": value, "duration_ms": round((time.perf_counter()-started)*1000, 2)})
            with patch("gov_mcp.core.BlankStore.counts", side_effect=TimeoutError) as attempts:
                started = time.perf_counter()
                response = await session.call_tool("search_evidence", {"tenant_id": "transport-test", "user_id": "citizen"})
                value = response.structuredContent or json.loads(response.content[0].text)
                assert attempts.call_count == 2 and (value["status"], value["message_code"]) == ("ERROR", "TOOL_TIMEOUT")
                records.append({"tool": "search_evidence", "result": value, "fault_scope": "injected technical storage timeout",
                                "actual_attempts": attempts.call_count, "duration_ms": round((time.perf_counter()-started)*1000, 2)})
    return records


def main():
    import socket
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    token = "transport-test-connection-token-" + "x" * 32
    with tempfile.TemporaryDirectory(prefix="gov-sse-technical-") as scratch:
        runtime = Path(scratch)
        (runtime / "role-map.json").write_text(json.dumps({"transport-test": {"citizen": "citizen", "staff": "staff"}}))
        server = uvicorn.Server(uvicorn.Config(create_app(runtime, token), host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            with httpx.Client(trust_env=False) as client:
                assert client.get(f"http://127.0.0.1:{port}/sse").status_code == 401
                assert client.post(f"http://127.0.0.1:{port}/messages/", headers={"Authorization": "Bearer incorrect"}).status_code == 401
                assert client.get(f"http://127.0.0.1:{port}/sse", headers={"Authorization": f"Bearer {token}", "Host": "untrusted.example"}).status_code == 421
            records = asyncio.run(verify(port, token))
            assert BlankStore(runtime / "business.sqlite3").counts() == {"evidence": 0, "cases": 0, "materials": 0}
            dest = Path("evidence/sse-transport.json")
            dest.parent.mkdir(exist_ok=True)
            dest.write_text(json.dumps({"scope": "isolated real SSE transport; not native Nexent agent acceptance",
                                        "passed": True, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Real SSE discovery and 6 tool calls passed, including bounded technical timeout; business tables remained empty.")
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()

"""Verify snapshot and MCP transport failures, restoring the isolated service."""

import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from gov_mcp.asset_query import AssetQuery
from nexent_api import NexentAPI, ROOT, RUNTIME


async def call_tool(token, identity):
    def client_factory(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    async with sse_client("http://127.0.0.1:8774/sse", headers={"Authorization": "Bearer " + token}, timeout=2, sse_read_timeout=8, httpx_client_factory=client_factory) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            response = await client.call_tool("govdocs_search_evidence", {"tenant_id": identity["tenant_id"], "user_id": identity["user_id"], "query": "申请表签字要求"})
            if response.isError:
                raise RuntimeError("MCP tool returned execution error")
            return response.structuredContent or json.loads(response.content[0].text)


def main():
    private = RUNTIME / "asset-query"
    index_path = private / "snapshot/index.json"
    original = index_path.read_bytes()
    version = AssetQuery(private).load()[0]["knowledge_version"]
    token = (private / "mcp-token.txt").read_text(encoding="ascii").strip()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "scope": "direct MCP fault checks, not native UI transport error acceptance", "checks": {}, "passed": False}
    api = NexentAPI()
    try:
        identity = api.login(json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))["developer"])
        try:
            index_path.write_text("{}", encoding="utf-8")
            value = asyncio.run(call_tool(token, identity))
            report["checks"]["invalid_snapshot_returns_error_without_citations"] = value.get("message_code") == "ASSET_SNAPSHOT_UNAVAILABLE" and not value.get("items") and not value.get("citations")
        finally:
            index_path.write_bytes(original)
        try:
            subprocess.run(["docker", "stop", "--time", "2", "govdocs-mcp"], capture_output=True, check=True, timeout=15)
            try:
                asyncio.run(call_tool(token, identity))
                report["checks"]["stopped_mcp_is_connection_failure"] = False
            except Exception as error:
                report["checks"]["stopped_mcp_is_connection_failure"] = True
                report["connection_error_type"] = type(error).__name__
        finally:
            subprocess.run(["docker", "start", "govdocs-mcp"], capture_output=True, check=True, timeout=15)
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            try:
                ready = httpx.get("http://127.0.0.1:8774/health", trust_env=False, timeout=2).is_success
                if ready:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        report["checks"]["service_recovered"] = ready
        report["checks"]["original_snapshot_and_version_restored"] = index_path.read_bytes() == original and AssetQuery(private).load()[0]["knowledge_version"] == version
        report["passed"] = all(report["checks"].values()) and len(report["checks"]) == 4
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        index_path.write_bytes(original)
        api.close()
        destination = ROOT / "evidence/asset-query/faults-20261015.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

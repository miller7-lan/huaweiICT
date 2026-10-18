"""Check actual authenticated SSE and register its read-only tools with existing Nexent.

Does not update or republish any existing agent, publish knowledge or run a language model.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from nexent_api import NexentAPI


ROOT = Path(__file__).resolve().parents[1]
NAME = "govkb_public_evidence_mcp"
TOOLS = {"govkb_get_environment_status", "govkb_search_evidence"}


async def verify(identities, token):
    def client(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    records = []
    async with sse_client("http://127.0.0.1:8773/sse", headers={"Authorization": "Bearer "+token}, httpx_client_factory=client) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = {tool.name for tool in (await session.list_tools()).tools}
            assert listed == TOOLS
            for tool, role, arguments, status, code in [
                ("govkb_get_environment_status", "citizen", {}, "OK", "PUBLISHED_KNOWLEDGE_READER_READY"),
                ("govkb_search_evidence", "citizen", {"query": "身份证到期换领需要什么材料"}, "EMPTY", "NO_PUBLISHED_KNOWLEDGE"),
                ("govkb_search_evidence", "staff", {"query": "身份证到期换领"}, "EMPTY", "NO_PUBLISHED_KNOWLEDGE"),
                ("govkb_search_evidence", "citizen", {"query": "未成年人身份证补领"}, "BLOCKED", "OUT_OF_CONFIRMED_SCOPE"),
                ("govkb_get_environment_status", "unmapped", {}, "BLOCKED", "ROLE_NOT_CONFIGURED"),
            ]:
                identity = identities[role] if role != "unmapped" else {"tenant_id": "foreign", "user_id": "not-configured"}
                response = await session.call_tool(tool, {"tenant_id": identity["tenant_id"], "user_id": identity["user_id"], **arguments})
                assert not response.isError
                value = response.structuredContent or json.loads(response.content[0].text)
                assert (value["status"], value["message_code"]) == (status, code)
                assert value["citations"] == value["items"] == []
                records.append({"tool": tool, "account_role": role, "result": value})
    with httpx.Client(trust_env=False, timeout=5) as http:
        unauthenticated = http.get("http://127.0.0.1:8773/sse").status_code
    assert unauthenticated == 401
    return records, unauthenticated


def main():
    target = ROOT / ".runtime/knowledge-update"
    accounts = json.loads((ROOT / ".runtime/m2/accounts.json").read_text(encoding="utf-8"))
    platform = json.loads((ROOT / ".runtime/m2/nexent-state.json").read_text(encoding="utf-8"))
    token = (target / "knowledge-mcp-token.txt").read_text(encoding="ascii").strip()
    api = NexentAPI(runtime_base="http://127.0.0.1:5018")
    try:
        identities = {role: api.login(accounts[role]) for role in ("citizen", "staff")}
        records, unauthenticated = asyncio.run(verify(identities, token))
        api.login(accounts["developer"])
        listed = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        if not any(service.get("remote_mcp_server_name", service.get("mcp_name")) == NAME for service in listed):
            api.call("POST", "/mcp/add", {"name": NAME, "server_url": "http://host.docker.internal:8773/sse",
                "authorization_token": "Bearer "+token, "enabled": True,
                "group_ids": str(platform["group_id"]), "ingroup_permission": "READ_ONLY"})
        api.call("GET", "/tool/scan_tool")
        listed = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        assert any(service.get("remote_mcp_server_name", service.get("mcp_name")) == NAME for service in listed)
        report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": True,
                  "registered_server": NAME, "tool_names": sorted(TOOLS), "records": records,
                  "unauthenticated_status": unauthenticated, "existing_agents_changed": False,
                  "model_run_performed": False, "business_review_completed": False,
                  "scope": "Actual authenticated SSE calls and Nexent MCP registration; no published business knowledge and no end-to-end model answer claimed."}
        output = ROOT / "evidence/knowledge-update/mcp-connection-2026-10-15.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "registered_server": NAME, "checks": len(records)+1, "existing_agents_changed": False}))
    finally:
        api.close()


if __name__ == "__main__":
    main()

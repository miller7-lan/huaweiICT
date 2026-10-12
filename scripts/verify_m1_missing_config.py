"""Capture real unconfigured resource results in the separate fault instance."""

import asyncio
import json
from datetime import datetime, timezone

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from nexent_api import NexentAPI, ROOT, RUNTIME


async def collect(user):
    token = (RUNTIME / "m1/missing-config/mcp-token.txt").read_text(encoding="ascii").strip()
    def factory(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    records = []
    async with sse_client("http://127.0.0.1:8768/sse", headers={"Authorization": "Bearer " + token},
                          httpx_client_factory=factory) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            for name in ("get_environment_status", "query_knowledge_graph", "prepare_update"):
                response = await client.call_tool("govx_" + name, {"tenant_id": user["tenant_id"], "user_id": user["user_id"]})
                value = response.structuredContent or json.loads(response.content[0].text)
                assert (value["status"], value["message_code"]) == ("ERROR", "CONNECTION_NOT_CONFIGURED")
                records.append({"tool": "govx_" + name, "result": value})
    return records


def main():
    api = NexentAPI()
    try:
        user = api.login(json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))["developer"])
    finally:
        api.close()
    records = asyncio.run(collect(user))
    audit = [json.loads(line) for line in (RUNTIME / "m1/missing-config/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    index = {a["request_id"]: a for a in audit}
    for record in records:
        record["audit"] = index[record["result"]["data"]["request_id"]]
        assert record["audit"]["result"] == record["result"]
    value = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": True,
        "scope": "Separate loopback fault instance; healthy extension and baseline configuration not withdrawn",
        "records": records, "workbench_audit": [json.loads(line) for line in
            (RUNTIME / "m1/missing-config/workbench-audit.jsonl").read_text(encoding="utf-8").splitlines()],
        "screenshot": "output/playwright/m1-BT13.png"}
    (ROOT / "evidence/m1-missing-config.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Missing-configuration SSE: environment, graph and updates return ERROR; no success/EMPTY claim.")


if __name__ == "__main__":
    main()

"""Read-only MCP regression after optional classification; no Agent publication."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.asset_query import AssetQuery
from nexent_api import NexentAPI


async def check(token, identity, version):
    def factory(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    cases = [("text", "演练事项由哪个窗口受理？", "text"),
             ("table", "演练分支甲需要什么证明？", "table"),
             ("image", "申请表需要本人签字吗？", "image"),
             ("missing", "办理费用是多少钱？", None)]
    rows = []
    async with sse_client("http://127.0.0.1:8774/sse", headers={"Authorization": "Bearer " + token},
            timeout=10, sse_read_timeout=60, httpx_client_factory=factory) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            for name, query, modality in cases:
                response = await client.call_tool("govdocs_search_evidence", {
                    "tenant_id": identity["tenant_id"], "user_id": identity["user_id"], "query": query, "limit": 4})
                value = response.structuredContent or json.loads(response.content[0].text)
                if modality:
                    passed = not response.isError and value["status"] == "OK" and value["knowledge_version"] == version and any(
                        item["modality"] == modality for item in value["citations"])
                else:
                    passed = not response.isError and value["status"] == "EMPTY" and not value["items"] and not value["citations"]
                rows.append({"case": name, "passed": passed, "status": value["status"],
                    "message_code": value["message_code"], "citation_count": len(value["citations"])})
    return rows


def main():
    runtime = ROOT / ".runtime/asset-query"
    files = [runtime / "snapshot" / name for name in ("index.json", "assets.json", "vectors.json")]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    index = AssetQuery(runtime).load()[0]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "direct MCP regression after classification; not a new native Agent/UI acceptance",
        "knowledge_version": index["knowledge_version"], "snapshot_hashes": hashes, "passed": False}
    api = NexentAPI()
    try:
        identity = api.login(json.loads((ROOT / ".runtime/accounts.json").read_text(encoding="utf-8"))["developer"])
        token = (runtime / "mcp-token.txt").read_text(encoding="ascii").strip()
        report["cases"] = asyncio.run(check(token, identity, index["knowledge_version"]))
        report["snapshot_unchanged"] = hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        report["passed"] = report["snapshot_unchanged"] and all(row["passed"] for row in report["cases"])
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        api.close()
    destination = ROOT / "evidence/jev-classification/retrieval-regression.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

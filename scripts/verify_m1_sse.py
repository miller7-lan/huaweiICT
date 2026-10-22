"""Actual govx SSE checks with platform-authenticated identities; no business fixtures."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from nexent_api import NexentAPI, ROOT, RUNTIME
from gov_mcp.core import BlankStore
from gov_mcp.extension import ExtensionStore


async def verify(identities):
    token = (RUNTIME / "m1/mcp-token.txt").read_text(encoding="ascii").strip()
    records = []
    def local_client(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    async with sse_client("http://127.0.0.1:8766/sse", headers={"Authorization": "Bearer " + token},
                          httpx_client_factory=local_client) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {"govx_" + n for n in ("get_environment_status", "search_evidence", "query_knowledge_graph",
                     "check_case_prerequisites", "prepare_update", "publish_update")}
            assert {t.name for t in listed.tools} == names
            cases = [
                ("get_environment_status", "citizen", {}, "OK", "ENVIRONMENT_READY"),
                ("search_evidence", "citizen", {}, "EMPTY", "NO_KNOWLEDGE"),
                ("query_knowledge_graph", "citizen", {}, "EMPTY", "NO_GRAPH"),
                ("check_case_prerequisites", "staff", {}, "BLOCKED", "MISSING_CASE_AND_MATERIALS"),
                ("check_case_prerequisites", "citizen", {}, "BLOCKED", "FORBIDDEN"),
                ("get_environment_status", "tenant_admin", {}, "BLOCKED", "ROLE_NOT_CONFIGURED"),
                ("prepare_update", "citizen", {"inputs": []}, "BLOCKED", "FORBIDDEN"),
                ("publish_update", "citizen", {"confirmed": True}, "BLOCKED", "FORBIDDEN"),
                ("prepare_update", "staff", {"inputs": []}, "BLOCKED", "FORBIDDEN"),
                ("publish_update", "staff", {"confirmed": True}, "BLOCKED", "FORBIDDEN"),
                ("prepare_update", "developer", {"inputs": []}, "EMPTY", "NO_CHANGES"),
                ("prepare_update", "developer", {"inputs": []}, "EMPTY", "NO_CHANGES"),
                ("prepare_update", "developer", {"inputs": []}, "EMPTY", "NO_CHANGES"),
                ("publish_update", "developer", {}, "BLOCKED", "PUBLISH_PREREQUISITES_MISSING"),
                ("publish_update", "developer", {"confirmed": True}, "BLOCKED", "PUBLISH_PREREQUISITES_MISSING"),
                ("publish_update", "developer", {"candidate_id": "nonexistent", "confirmed": True}, "BLOCKED", "CANDIDATE_NOT_FOUND"),
            ]
            for name, role, arguments, status, code in cases:
                identity = identities[role]
                response = await session.call_tool("govx_" + name, {"tenant_id": identity["tenant_id"],
                                                    "user_id": identity["user_id"], **arguments})
                assert not response.isError
                value = response.structuredContent or json.loads(response.content[0].text)
                assert (value["status"], value["message_code"]) == (status, code)
                assert value["items"] == value["citations"] == [] and value["knowledge_version"] is None
                records.append({"tool": "govx_" + name, "account_role": role, "result": value})
    return records


def main():
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    api = NexentAPI()
    try:
        identities = {r: api.login(accounts[r]) for r in ("citizen", "staff", "developer", "tenant_admin")}
    finally:
        api.close()
    store = ExtensionStore(RUNTIME / "m1/mcp/extension.sqlite3")
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    records = asyncio.run(verify(identities))
    after = hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert before == after and not any(store.counts().values())
    audit = [json.loads(line) for line in (RUNTIME / "m1/mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    index = {r["request_id"]: r for r in audit}
    for record in records:
        entry = index[record["result"]["data"]["request_id"]]
        principal = identities[record["account_role"]]
        assert entry["result"] == record["result"]
        assert entry["principal_sha256"] == hashlib.sha256(f"{principal['tenant_id']}\0{principal['user_id']}".encode()).hexdigest()
        record["audit"] = entry
    original_counts = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
    assert not any(original_counts.values())
    output = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": True,
              "scope": "Live isolated MCP SSE with native login identities; not a replacement for native Agent or UI acceptance",
              "actual_calls": len(records), "records": records, "extension_counts": store.counts(),
              "database_sha256_before": before, "database_sha256_after": after, "original_counts": original_counts}
    (ROOT / "evidence/m1-sse.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"M1 live SSE: {len(records)} calls passed; audit matched; all business/candidate/version counts zero.")


if __name__ == "__main__":
    main()

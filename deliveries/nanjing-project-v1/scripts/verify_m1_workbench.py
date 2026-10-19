"""Real workbench permission and authenticated identity checks, no UI replacement."""

import json
from datetime import datetime, timezone

import httpx

from nexent_api import ROOT, RUNTIME
from gov_mcp.extension import ExtensionStore


def main():
    accounts = json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))
    origin = "http://127.0.0.1:8767"
    records = []
    for role in ("staff", "fresh_citizen", "developer", "tenant_admin"):
        with httpx.Client(base_url=origin, trust_env=False, timeout=20, headers={"Origin": origin}) as client:
            assert client.get("/api/state").status_code == 401
            response = client.post("/api/login", json=accounts[role])
            assert response.status_code == 200
            assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
            value = client.get("/api/state").json()
            expected_role = {"staff": "staff", "fresh_citizen": "citizen", "developer": "staff", "tenant_admin": None}[role]
            assert value["data"].get("role") == expected_role
            records.append({"account_role": role, "operation": "state", "result": value})
            value = client.get("/api/workbench").json()
            expected_code = "MISSING_CASE_AND_MATERIALS" if expected_role == "staff" else (
                "ROLE_NOT_CONFIGURED" if expected_role is None else "FORBIDDEN")
            assert value["message_code"] == expected_code
            records.append({"account_role": role, "operation": "workbench", "result": value})
            for name, body in (("prepare_update", {"inputs": []}), ("publish_update", {"confirmed": True})):
                value = client.post("/api/tools/" + name, json=body).json()
                code = ("NO_CHANGES" if name == "prepare_update" else "PUBLISH_PREREQUISITES_MISSING") if role == "developer" else (
                    "ROLE_NOT_CONFIGURED" if expected_role is None else "FORBIDDEN")
                assert value["message_code"] == code
                records.append({"account_role": role, "operation": name, "result": value})
            for name, body in (("prepare_update", {"tenant_id": "forged", "user_id": "forged", "inputs": []}),
                               ("query_knowledge_graph", {"action": "insert"})):
                response = client.post("/api/tools/" + name, json=body)
                assert response.status_code == 400 and response.json()["message_code"] == "INVALID_ARGUMENTS"
                records.append({"account_role": role, "operation": "forged-" + name,
                                "http_status": response.status_code, "result": response.json()})
            response = client.post("/api/tools/prepare_update", json={"inputs": []}, headers={"Origin": "http://evil.example"})
            assert response.status_code == 403
            records.append({"account_role": role, "operation": "cross-origin", "http_status": 403})
            assert client.post("/api/logout", json={}).status_code == 200
            assert client.get("/api/state").status_code == 401
    counts = ExtensionStore(RUNTIME / "m1/mcp/extension.sqlite3").counts()
    assert not any(counts.values())
    output = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": True,
        "scope": "Live workbench HTTP tests; screenshots and native Agent traces are assessed separately",
        "records": records, "extension_counts": counts}
    (ROOT / "evidence/m1-workbench-api.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Workbench: 4 native identities, role/maintenance isolation, injection and CSRF checks passed.")


if __name__ == "__main__":
    main()

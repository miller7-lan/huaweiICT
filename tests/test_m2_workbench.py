import json

import httpx
from starlette.testclient import TestClient

from gov_mcp.m2_workbench import create_app


def test_login_required_and_cross_origin_mutations_blocked(tmp_path):
    (tmp_path / "mcp-token.txt").write_text("x"*48)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8771") as client:
        assert client.get("/api/state").status_code == 401
        denied = client.post("/api/tools/create_task", json={"question": "test"}, headers={"Origin": "https://external.example"})
        assert denied.status_code == 403
        assert denied.json()["message_code"] == "ORIGIN_REJECTED"
        assert client.post("/api/tools/create_task", json={}, headers={"Origin": "http://127.0.0.1:8771"}).status_code == 401


def test_authenticated_api_rejects_identity_and_role_fields(tmp_path, monkeypatch):
    (tmp_path / "mcp-token.txt").write_text("x"*48)
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp/role-map.json").write_text(json.dumps({"tenant": {"citizen": {"role": "citizen"}}}))

    class AuthOnlyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"data": {"session": {"access_token": "test-session"}}}, request=httpx.Request("POST", url))

        async def get(self, url, **kwargs):
            return httpx.Response(200, json={"data": {"user": {"tenant_id": "tenant", "user_id": "citizen"}}}, request=httpx.Request("GET", url))

    monkeypatch.setattr("gov_mcp.m2_workbench.httpx.AsyncClient", AuthOnlyClient)
    origin = {"Origin": "http://127.0.0.1:8771"}
    with TestClient(create_app(tmp_path), base_url=origin["Origin"]) as client:
        login = client.post("/api/login", json={"email": "test@example.com", "password": "not-a-real-password"}, headers=origin)
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=strict" in login.headers["set-cookie"]
        for field in ("role", "tenant_id", "user_id", "knowledge_version"):
            value = client.post("/api/tools/create_task", json={"question": "test", field: "forged"}, headers=origin)
            assert value.status_code == 400
            assert value.json()["message_code"] == "INVALID_ARGUMENTS"
        invalid = client.post("/api/agent", json={"role": "staff"}, headers=origin)
        assert invalid.status_code == 400

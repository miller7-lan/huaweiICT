"""HTTP/authentication controls, with explicit local engineering accounts."""
from datetime import datetime
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from gov_mcp.matter_auth import LocalEngineeringAuth, NexentAuth
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal, LifecycleError
from gov_mcp.matter_web import create_app

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
BASE = "http://127.0.0.1:8783"
NOW = datetime.fromisoformat("2026-10-19T13:00:00+00:00").timestamp()
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
    "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu"}
PASSWORD = "test-fixture-not-a-real-password"


@pytest.fixture
def web(tmp_path):
    service = MatterLifecycle(tmp_path / "service", clock=lambda: NOW)
    maint = Principal("test-initializer", "maintainer", "automated_test")
    candidate = service.prepare(PACK, ontology_stage="evolved", principal=maint)
    service.release_preview(candidate["candidate_id"], None, note="test fixture", principal=maint)
    salt = b"fixture-only-salt"
    accounts = {username: {"id": "test-" + username, "role": role, "enabled": True,
        "salt": salt.hex(), "password_hash": hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, 240000).hex()}
        for username, role in (("citizen", "citizen"), ("other", "citizen"), ("staff", "staff"), ("maint", "maintainer"))}
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"mode": "local_engineering_accounts", "accounts": accounts}), encoding="utf-8")
    tick = [100.0]
    app = create_app(service, LocalEngineeringAuth(path), session_clock=lambda: tick[0])
    return app, service, path, tick


def client(app):
    return TestClient(app, base_url=BASE, headers={"Origin": BASE})


def login(c, username="citizen"):
    r = c.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200
    session = c.get("/api/session").json()
    c.headers["X-CSRF-Token"] = session["csrf"]
    return session


def test_no_public_role_claim_or_identity_injection(web):
    app, _, _, _ = web
    with client(app) as c:
        assert not c.get("/api/session").json()["logged_in"]
        assert c.get("/api/state").status_code == 401
        assert c.post("/api/login", json={"username": "citizen", "password": PASSWORD, "role": "maintainer"}).status_code == 401
        session = login(c)
        assert session["role"] == "citizen"
        injected = c.post("/api/query", json={"facts": FACTS, "topic": "materials", "role": "maintainer"})
        assert injected.status_code == 400
        assert c.post("/api/source", json={"source_id": "NJ-OLD-CARD-2025", "enabled": False, "reason": "unauthorized"}).status_code == 403


def test_origin_csrf_host_and_request_size(web):
    app, _, _, _ = web
    with client(app) as c:
        login(c)
        payload = {"facts": FACTS, "topic": "materials"}
        assert c.post("/api/query", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/query", json=payload, headers={"X-CSRF-Token": "forged"}).status_code == 403
        assert c.post("/api/query", json=payload, headers={"Host": "evil.example"}).status_code == 421
        assert c.post("/api/query", content="x" * 4097, headers={"Content-Type": "application/json"}).status_code == 413


def test_authenticated_static_source_guidance_and_secure_cookie(web):
    app, _, _, _ = web
    with client(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/assets/matter_web.js").status_code == 200
        assert c.get("/assets/../../.env").status_code == 404
        login(c)
        response = c.post("/api/query", json={"facts": FACTS, "topic": "materials"})
        assert response.status_code == 200 and len(response.json()["sections"]) == 3
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert not response.json()["formal_business_answer"]
        assert c.get("/api/state").json()["cases"]
        assert c.get("/api/session").json()["authentication_mode"] == "local_engineering_accounts"


def test_cross_account_read_and_handoff_code_isolation(web):
    app, _, _, _ = web
    with client(app) as citizen, client(app) as other, client(app) as staff:
        login(citizen)
        login(other, "other")
        login(staff, "staff")
        case = citizen.post("/api/query", json={"facts": FACTS, "topic": "materials"}).json()
        case_id = case["case_id"]
        assert other.post("/api/read", json={"case_id": case_id}).status_code == 403
        assert staff.post("/api/read", json={"case_id": case_id}).status_code == 403
        assert not other.get("/api/state").json()["cases"]
        handoff = citizen.post("/api/handoff", json={"case_id": case_id}).json()
        assert staff.post("/api/accept", json={"case_id": case_id, "token": "x" * 32}).status_code == 409
        accepted = staff.post("/api/accept", json={"case_id": case_id, "token": handoff["handoff_token"]})
        assert accepted.status_code == 200 and accepted.json()["version"] == case["version"]
        assert accepted.json()["declared_conditions"] == FACTS
        assert "owner" not in accepted.json() and "assignee" not in accepted.json()
        assert staff.post("/api/accept", json={"case_id": case_id, "token": handoff["handoff_token"]}).status_code == 409
        assert staff.post("/api/confirm", json={"case_id": case_id, "acknowledge": False, "note": "test only"}).status_code == 409
        done = staff.post("/api/confirm", json={"case_id": case_id, "acknowledge": True, "note": "Automated HTTP fixture"})
        assert done.json()["state"] == "staff_confirmed" and not done.json()["formal_business_approval"]


def test_current_pause_blocks_saved_browser_case(web):
    app, _, _, _ = web
    with client(app) as citizen, client(app) as maint:
        login(citizen)
        login(maint, "maint")
        case = citizen.post("/api/query", json={"facts": FACTS, "topic": "materials"}).json()
        assert maint.post("/api/source", json={"source_id": "NJ-POLICE-FAQ-2025", "enabled": False, "reason": "controlled HTTP fixture pause"}).status_code == 200
        blocked = citizen.post("/api/read", json={"case_id": case["case_id"]})
        assert blocked.status_code == 409 and blocked.json()["code"] == "SOURCE_PAUSED"


def test_disabled_account_and_session_expiry_are_revalidated(web):
    app, _, path, tick = web
    with client(app) as c:
        login(c)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["accounts"]["citizen"]["enabled"] = False
        path.write_text(json.dumps(data), encoding="utf-8")
        assert not c.get("/api/session").json()["logged_in"]
        assert c.post("/api/query", json={"facts": FACTS, "topic": "materials"}).status_code == 401
        data["accounts"]["citizen"]["enabled"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        login(c)
        tick[0] += 1801
        assert not c.get("/api/session").json()["logged_in"]


def test_logout_invalidates_server_session(web):
    app, _, _, _ = web
    with client(app) as c:
        login(c)
        assert c.post("/api/logout", json={}).status_code == 200
        assert not c.get("/api/session").json()["logged_in"]


def test_login_rate_limit(web):
    app, _, _, _ = web
    with client(app) as c:
        for _ in range(8):
            assert c.post("/api/login", json={"username": "citizen", "password": "wrong"}).status_code == 401
        assert c.post("/api/login", json={"username": "citizen", "password": "wrong"}).status_code == 429


def test_nexent_adapter_uses_server_identity_and_trusted_role_map(monkeypatch, tmp_path):
    role_file = tmp_path / "roles.json"
    role_file.write_text(json.dumps({"fixture-tenant": {"fixture-user": {"role": "staff"}}}), encoding="utf-8")
    requests = []
    def transport(request):
        requests.append(request.url.path)
        if request.url.path == "/user/signin":
            return httpx.Response(200, json={"data": {"session": {"access_token": "fixture-token-only"}}})
        return httpx.Response(200, json={"data": {"user": {"tenant_id": "fixture-tenant", "user_id": "fixture-user"}}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr("gov_mcp.matter_auth.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(transport)))
    provider = NexentAuth(role_file)
    async def exercise():
        credential = await provider.login("fixture", "fixture-password")
        principal = await provider.resolve(credential)
        assert principal.role == "staff" and principal.kind == "service"
        assert principal.id != "fixture-user"
    asyncio.run(exercise())
    assert requests == ["/user/signin", "/user/current_user_info", "/user/current_user_info"]


def test_nexent_adapter_does_not_accept_unmapped_or_remote_identity(monkeypatch, tmp_path):
    role_file = tmp_path / "roles.json"
    role_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        NexentAuth(role_file, "http://untrusted.example")
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200,
        json={"data": {"user": {"tenant_id": "fixture-tenant", "user_id": "fixture-user"}}}))
    monkeypatch.setattr("gov_mcp.matter_auth.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport))
    with pytest.raises(LifecycleError, match="没有分配"):
        asyncio.run(NexentAuth(role_file).resolve({"token": "fixture-token-only"}))

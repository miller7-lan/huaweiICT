"""The clean-checkout demo must remain isolated from business data and policies."""

import json

import pytest
from starlette.testclient import TestClient

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_demo import check, create_app, demo_store, initialize, query, refresh, update


def test_offline_roles_versions_and_literal_originals(tmp_path):
    runtime = tmp_path / "demo"
    first = initialize(runtime)
    assert first["passed"] and first["counts"]["documents"] == 1
    assert first["network_requests"] == first["model_requests"] == 0
    assert not first["business_approval"] and not first["heartbeat_registered"]
    later = update(runtime)
    assert later["passed"] and later["counts"]["revisions"] == 2
    assert later["checks"]["pending_update_stopped"]
    assert later["checks"]["old_version_preserved"]
    assert "模拟材料乙" in query(runtime, "citizen")["result"]["data"]["answer"]
    assert check(runtime)["counts"]["revisions"] == 2


def test_expired_local_observation_requires_explicit_replay(tmp_path):
    runtime = tmp_path / "demo"
    initialize(runtime)
    store, _ = demo_store(runtime)
    with store.db() as db:
        db.execute("UPDATE sites SET last_success=0")
    with pytest.raises(ValueError, match="自检未通过"):
        check(runtime)
    assert refresh(runtime)["passed"]
    assert check(runtime)["counts"]["revisions"] == 1


def test_reject_existing_and_nested_business_runtime(tmp_path):
    production = tmp_path / "production"
    store = KnowledgeStore.initialize(production)
    policy = (production / "publication-policy.json").read_bytes()
    with pytest.raises(ValueError, match="空目录"):
        initialize(production)
    with pytest.raises(ValueError, match="业务运行目录"):
        initialize(production / "nested-demo")
    assert (production / "publication-policy.json").read_bytes() == policy
    assert store.status()["counts"]["documents"] == 0
    assert not (production / "team-demo.json").exists()


def test_wrong_policy_and_foreign_source_refused(tmp_path):
    runtime = tmp_path / "demo"
    initialize(runtime)
    path = runtime / "publication-policy.json"
    original = path.read_bytes()
    path.write_text(json.dumps({"schema": 1, "mode": "business"}), encoding="utf-8")
    with pytest.raises(ValueError, match="独立"):
        demo_store(runtime)
    path.write_bytes(original)
    store, _ = demo_store(runtime)
    with store.db() as db:
        foreign = json.loads(db.execute("SELECT config FROM sites").fetchone()[0])
    store.register(dict(foreign, id="OTHER-SOURCE"))
    with pytest.raises(ValueError, match="其他来源"):
        demo_store(runtime)


def test_demo_browser_keeps_warning_and_read_only_boundary(tmp_path):
    runtime = tmp_path / "demo"
    initialize(runtime)
    with TestClient(create_app(runtime)) as client:
        assert "全部材料与费用均为虚构" in client.get("/").text
        assert client.get("/health").json()["mode"] == "synthetic_team_demo_v1"
        for role in ("citizen", "staff"):
            response = client.get("/api/query", params={"role": role})
            assert response.status_code == 200 and response.json()["demo"] is True
            assert response.json()["result"]["data"]["role"] == role
        assert client.get("/api/query", params={"role": "admin"}).status_code == 400
        assert client.get("/api/query", params={"version": "foreign-version"}).status_code == 400
        assert client.post("/api/query").status_code == 405
        assert client.get("/api/status", headers={"host": "foreign.example"}).status_code == 400

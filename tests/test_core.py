"""Verify authorization, terminal states, storage boundaries, and bounded retries."""

import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gov_mcp.core import BlankStore, GovernmentTools
from gov_mcp.server import create_app


@pytest.fixture
def tools(tmp_path):
    roles = tmp_path / "role-map.json"
    roles.write_text(json.dumps({"test-tenant": {"citizen-id": "citizen", "staff-id": "staff"}}))
    return GovernmentTools(BlankStore(tmp_path / "business.sqlite3"), roles, retry_delay=0)


def test_empty_environment_is_ready(tools):
    value = tools.get_environment_status("test-tenant", "citizen-id")
    assert value["status"] == "OK"
    assert value["data"]["role"] == "citizen"
    assert all(r["configured"] and r["count"] == 0 for r in value["data"]["resources"].values())


def test_empty_evidence_is_terminal(tools):
    value = tools.search_evidence("test-tenant", "citizen-id")
    assert value["status"] == "EMPTY"
    assert value["items"] == value["citations"] == []
    assert value["knowledge_version"] is None


def test_staff_missing_prerequisites(tools):
    value = tools.check_case_prerequisites("test-tenant", "staff-id")
    assert value["status"] == "BLOCKED"
    assert value["message_code"] == "MISSING_CASE_AND_MATERIALS"


def test_citizen_cannot_use_staff_tool(tools):
    value = tools.check_case_prerequisites("test-tenant", "citizen-id")
    assert (value["status"], value["message_code"]) == ("BLOCKED", "FORBIDDEN")


@pytest.mark.parametrize("tenant,user", [("test-tenant", "unmapped"), ("other-tenant", "staff-id"), ("", "staff-id")])
def test_unmapped_or_cross_tenant_stops(tools, tenant, user):
    assert tools.get_environment_status(tenant, user)["message_code"] == "ROLE_NOT_CONFIGURED"


def test_repeated_calls_do_not_write_business_records(tools):
    for _ in range(10):
        assert tools.search_evidence("test-tenant", "citizen-id")["status"] == "EMPTY"
    assert tools.store.counts() == {"evidence": 0, "cases": 0, "materials": 0}


def test_timeout_retries_once_then_stops(tools, monkeypatch):
    attempts = []
    def timeout():
        attempts.append(1)
        raise TimeoutError()
    monkeypatch.setattr(tools.store, "counts", timeout)
    value = tools.search_evidence("test-tenant", "citizen-id")
    assert len(attempts) == 2
    assert (value["status"], value["message_code"]) == ("ERROR", "TOOL_TIMEOUT")


def test_transient_timeout_recovers_on_second_attempt(tools, monkeypatch):
    counts = tools.store.counts
    attempts = []
    def transient():
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError()
        return counts()
    monkeypatch.setattr(tools.store, "counts", transient)
    assert tools.search_evidence("test-tenant", "citizen-id")["status"] == "EMPTY"
    assert len(attempts) == 2


def test_real_sqlite_lock_retries_twice_then_stops(tools, monkeypatch):
    tools.store.timeout = .02
    attempts = []
    counts = tools.store.counts
    def counted():
        attempts.append(1)
        return counts()
    monkeypatch.setattr(tools.store, "counts", counted)
    with closing(sqlite3.connect(tools.store.path)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        try:
            value = tools.search_evidence("test-tenant", "citizen-id")
        finally:
            lock.rollback()
    assert len(attempts) == 2
    assert (value["status"], value["message_code"], value["data"]["attempts"]) == ("ERROR", "TOOL_TIMEOUT", 2)


def test_nonlock_sqlite_error_is_not_retried(tools, monkeypatch):
    attempts = []
    def invalid():
        attempts.append(1)
        raise sqlite3.OperationalError("no such table: evidence")
    monkeypatch.setattr(tools.store, "counts", invalid)
    assert tools.search_evidence("test-tenant", "citizen-id")["message_code"] == "CONFIG_OR_STORAGE_UNAVAILABLE"
    assert len(attempts) == 1


def test_nonempty_storage_is_rejected(tools):
    # This test uses an empty technical row, never a policy or business example.
    with closing(sqlite3.connect(tools.store.path)) as db:
        db.execute("INSERT INTO evidence VALUES ('boundary-check', '')")
        db.commit()
    assert tools.search_evidence("test-tenant", "citizen-id")["message_code"] == "BLANK_BOUNDARY_VIOLATED"


def test_invalid_role_configuration_is_error(tools):
    tools.role_map_path.write_text("not json")
    assert tools.get_environment_status("test-tenant", "citizen-id")["status"] == "ERROR"


@pytest.mark.parametrize("invalid", [[], {"test-tenant": []}, {"test-tenant": None}])
def test_invalid_role_map_shape_fails_closed(tools, invalid):
    tools.role_map_path.write_text(json.dumps(invalid))
    assert tools.get_environment_status("test-tenant", "citizen-id")["status"] == "ERROR"


def test_missing_store_is_error_not_empty(tools):
    tools.store.path.unlink()
    assert tools.search_evidence("test-tenant", "citizen-id")["status"] == "ERROR"


def test_sse_transport_requires_connection_token(tmp_path):
    app = create_app(tmp_path, "x" * 40)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/sse").status_code == 401
        assert client.post("/messages/", headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("positional", [False, True])
def test_nexent_identity_wrapper_overwrites_model_identity(positional):
    source = Path("vendor/nexent/sdk/nexent/core/agents/tool_user_context.py")
    assert source.exists(), "The exact Nexent v2.7.0 source is required for this integration check"
    spec = importlib.util.spec_from_file_location("nexent_identity_wrapper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Tool:
        inputs = {"tenant_id": {}, "user_id": {}, "query": {}}
        def forward(self, *args, **kwargs):
            return dict(args[0]) if args else kwargs
    wrapped = module.apply_user_context_to_mcp_tool(Tool(), {"tenant_id": "test-tenant", "user_id": "citizen-id"})
    assert set(wrapped.inputs) == {"query"}
    supplied = {"tenant_id": "other-tenant", "user_id": "staff-id", "query": "technical self-check"}
    value = wrapped.forward(supplied) if positional else wrapped.forward(**supplied)
    assert value == {"tenant_id": "test-tenant", "user_id": "citizen-id", "query": "technical self-check"}

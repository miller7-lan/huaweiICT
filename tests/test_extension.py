import json
import sqlite3
from contextlib import closing

import pytest
from starlette.testclient import TestClient

from gov_mcp.extension import ExtensionTools, TABLES
from gov_mcp.extension_server import create_app


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "role-map.json").write_text(json.dumps({"t": {
        "citizen": {"role": "citizen"}, "staff": {"role": "staff"},
        "maintainer": {"role": "staff", "knowledge_maintainer": True}}}))
    (tmp_path / "connections.json").write_text(json.dumps({"evidence": True, "graph": True, "updates": True}))
    return ExtensionTools(tmp_path, retry_delay=0, timeout=.02)


def test_empty_graph_and_unconfigured_are_distinct(tools):
    value = tools.execute("query_knowledge_graph", "t", "citizen")
    assert (value["status"], value["message_code"]) == ("EMPTY", "NO_GRAPH")
    assert value["data"]["nodes"] == value["data"]["edges"] == []
    (tools.runtime / "connections.json").write_text(json.dumps({"evidence": True, "graph": False, "updates": True}))
    value = tools.execute("query_knowledge_graph", "t", "citizen")
    assert (value["status"], value["message_code"]) == ("ERROR", "CONNECTION_NOT_CONFIGURED")


@pytest.mark.parametrize("user", ["citizen", "staff"])
@pytest.mark.parametrize("tool", ["prepare_update", "publish_update"])
def test_staff_role_is_not_maintenance_permission(tools, user, tool):
    assert tools.execute(tool, "t", user)["message_code"] == "FORBIDDEN"
    assert not any(tools.store.counts().values())


def test_role_and_maintenance_grants_fail_closed(tools):
    assert tools.execute("get_environment_status", "t", "unknown")["message_code"] == "ROLE_NOT_CONFIGURED"
    (tools.runtime / "role-map.json").write_text(json.dumps({"t": {"citizen": {"role": "citizen", "knowledge_maintainer": "true"}}}))
    assert tools.execute("prepare_update", "t", "citizen")["status"] == "ERROR"


def test_empty_updates_and_all_publish_preconditions_have_no_side_effect(tools):
    before = tools.store.path.read_bytes()
    for _ in range(3):
        value = tools.execute("prepare_update", "t", "maintainer", inputs=[])
        assert value["message_code"] == "NO_CHANGES" and value["status"] == "EMPTY"
    value = tools.execute("publish_update", "t", "maintainer")
    assert value["data"]["missing"] == ["candidate", "human_confirmation"]
    assert tools.execute("publish_update", "t", "maintainer", confirmed=True)["status"] == "BLOCKED"
    assert tools.execute("publish_update", "t", "maintainer", candidate_id="nonexistent", confirmed=True)["message_code"] == "CANDIDATE_NOT_FOUND"
    assert tools.execute("prepare_update", "t", "maintainer", inputs=["forbidden-input"])["message_code"] == "M1_INPUT_NOT_ALLOWED"
    assert tools.store.path.read_bytes() == before
    assert tools.store.counts() == dict.fromkeys(TABLES, 0)


def test_missing_and_invalid_connections_never_succeed(tools):
    (tools.runtime / "connections.json").unlink()
    assert tools.execute("get_environment_status", "t", "staff")["message_code"] == "CONFIG_OR_STORAGE_UNAVAILABLE"


def test_actual_storage_lock_retries_twice_then_recovers(tools):
    with closing(sqlite3.connect(tools.store.path)) as locker:
        locker.execute("BEGIN EXCLUSIVE")
        value = tools.execute("search_evidence", "t", "citizen")
        assert value["message_code"] == "TOOL_TIMEOUT" and value["data"]["attempts"] == 2
        locker.rollback()
    assert tools.execute("search_evidence", "t", "citizen")["message_code"] == "NO_KNOWLEDGE"


def test_nonempty_extension_storage_is_rejected(tools):
    # Technical candidate identifier only, confined to a temporary test DB.
    with closing(sqlite3.connect(tools.store.path)) as db:
        db.execute("INSERT INTO candidates VALUES ('technical-fixture', 'pending')")
        db.commit()
    assert tools.execute("get_environment_status", "t", "staff")["message_code"] == "BLANK_BOUNDARY_VIOLATED"


def test_service_token_and_host_are_required(tools):
    with TestClient(create_app(tools.runtime, "x" * 48)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer " + "x" * 48, "Host": "evil.example"}).status_code == 421

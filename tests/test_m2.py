import json
from pathlib import Path

import pytest

from gov_mcp.m2 import M2Store, M2Tools


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "phase.json").write_text(json.dumps({"phase": "M2", "mode": "engineering_demo"}))
    (tmp_path / "role-map.json").write_text(json.dumps({"tenant": {
        "citizen": {"role": "citizen"}, "other": {"role": "citizen"}, "staff": {"role": "staff"}},
        "foreign": {"staff": {"role": "staff"}}}))
    tool = M2Tools(tmp_path)
    sources = [{"source_id": "official", "title": "官方测试条款", "url": "https://example.gov.cn/document",
        "region": "national", "document_type": "statute", "sha256": "a"*64,
        "review_status": "pending_business_review", "tags": {"applicability": "demo"}}]
    chunks = [{"source_id": "official", "chunk_id": "clause", "locator": "测试位置",
        "category": "identity", "region": "national", "text": "身份证到期换领的测试原文，限临时单元测试数据。"}]
    tool.store.seed_preview(sources, chunks)
    return tool


def task(tools, user="citizen"):
    value = tools.execute("create_task", "tenant", user, question="身份证到期换领")
    assert value["status"] == "OK"
    return value["data"]["task_id"], value["knowledge_version"]


def handoff(tools):
    tid, version = task(tools)
    tools.execute("declare_materials", "tenant", "citizen", task_id=tid,
        materials={"identity_card": "missing", "household_register": "unknown"})
    value = tools.execute("create_handoff", "tenant", "citizen", task_id=tid, idempotency_key="demo-key-01")
    assert value["status"] == "OK"
    return tid, version, value["data"]["handoff_id"]


def test_phase_marker_refuses_original_or_m1_runtime(tmp_path):
    (tmp_path / "phase.json").write_text(json.dumps({"phase": "M1", "mode": "engineering_demo"}))
    with pytest.raises(ValueError):
        M2Store(tmp_path)
    assert not (tmp_path / "m2.sqlite3").exists()


def test_owned_task_pins_version_across_new_preview(tools):
    tid, old = task(tools)
    with tools.store.connect(write=True) as db:
        db.execute("INSERT INTO versions VALUES('preview-new','demo_preview','new','later')")
    value = tools.execute("search_evidence", "tenant", "citizen", task_id=tid)
    assert value["knowledge_version"] == old
    assert all(c["knowledge_version"] == old for c in value["citations"])


def test_other_citizen_and_foreign_staff_cannot_read_task_or_handoff(tools):
    tid, _, hid = handoff(tools)
    assert tools.execute("search_evidence", "tenant", "other", task_id=tid)["message_code"] == "TASK_NOT_FOUND"
    assert tools.execute("check_case_prerequisites", "foreign", "staff", handoff_id=hid)["message_code"] == "HANDOFF_NOT_FOUND"


@pytest.mark.parametrize("action", ["list_handoffs", "check_case_prerequisites", "confirm_review"])
def test_citizen_staff_actions_denied_before_any_data_return(tools, action):
    value = tools.execute(action, "tenant", "citizen")
    assert value["message_code"] == "FORBIDDEN"
    assert value["citations"] == value["items"] == []


def test_unmapped_identity_never_gets_business_role(tools):
    assert tools.execute("get_environment_status", "tenant", "unknown")["message_code"] == "ROLE_NOT_CONFIGURED"


def test_handoff_replay_does_not_duplicate_and_freezes_material_snapshot(tools):
    tid, version, hid = handoff(tools)
    again = tools.execute("create_handoff", "tenant", "citizen", task_id=tid, idempotency_key="demo-key-01")
    assert again["data"]["handoff_id"] == hid
    assert tools.store.counts()["handoffs"] == 1
    changed = tools.execute("declare_materials", "tenant", "citizen", task_id=tid,
        materials={"identity_card": "present", "household_register": "present"})
    assert changed["message_code"] == "TASK_ALREADY_HANDED_OFF"
    assessment = tools.execute("check_case_prerequisites", "tenant", "staff", handoff_id=hid)
    assert assessment["knowledge_version"] == version
    assert assessment["data"]["materials"]["identity_card"] == "missing"
    assert assessment["data"]["source_review_pending"]
    assert assessment["data"]["formal_approval"] is False


def test_same_idempotency_key_for_different_task_is_conflict(tools):
    handoff(tools)
    tid, _ = task(tools)
    value = tools.execute("create_handoff", "tenant", "citizen", task_id=tid, idempotency_key="demo-key-01")
    assert value["message_code"] == "IDEMPOTENCY_CONFLICT"
    assert tools.store.counts()["handoffs"] == 1


def test_confirm_requires_explicit_boolean_and_exact_assessment_digest(tools):
    _, version, hid = handoff(tools)
    assessment = tools.execute("check_case_prerequisites", "tenant", "staff", handoff_id=hid)
    sha = assessment["data"]["assessment_sha256"]
    for flag, supplied in ((False, sha), (1, sha), (True, "wrong")):
        value = tools.execute("confirm_review", "tenant", "staff", handoff_id=hid,
            assessment_sha256=supplied, confirmed=flag)
        assert value["message_code"] == "CONFIRMATION_OR_ASSESSMENT_MISMATCH"
    confirmed = tools.execute("confirm_review", "tenant", "staff", handoff_id=hid,
        assessment_sha256=sha, confirmed=True)
    assert confirmed["message_code"] == "DEMO_REVIEW_RECORDED"
    assert confirmed["knowledge_version"] == version
    assert confirmed["data"]["business_rule_review_completed"] is False
    assert tools.store.counts()["reviews"] == 1
    replay = tools.execute("confirm_review", "tenant", "staff", handoff_id=hid,
        assessment_sha256=sha, confirmed=True)
    assert replay["status"] == "OK"
    assert tools.store.counts()["reviews"] == 1


def test_empty_retrieval_blocks_handoff_instead_of_inventing_citations(tools):
    tid, _ = task(tools)
    with tools.store.connect(write=True) as db:
        db.execute("UPDATE tasks SET question='火星移民外星船舶' WHERE task_id=?", (tid,))
    assert tools.execute("search_evidence", "tenant", "citizen", task_id=tid)["status"] == "EMPTY"
    value = tools.execute("create_handoff", "tenant", "citizen", task_id=tid, idempotency_key="demo-key-02")
    assert value["message_code"] == "NO_SUPPORTING_EVIDENCE"
    assert tools.store.counts()["handoffs"] == 0


def test_material_input_rejects_extra_personal_fields(tools):
    tid, _ = task(tools)
    value = tools.execute("declare_materials", "tenant", "citizen", task_id=tid,
        materials={"identity_card": "present", "household_register": "unknown", "applicant_name": "test"})
    assert value["message_code"] == "INVALID_MATERIAL_DECLARATION"


def test_classification_keeps_actual_source_region_and_pending_review(tools):
    tid, _ = task(tools)
    value = tools.execute("search_evidence", "tenant", "citizen", task_id=tid)
    citation = value["citations"][0]
    assert (citation["source_id"], citation["region"], citation["category"]) == ("official", "national", "identity")
    assert citation["review_status"] == "pending_business_review"
    audit = [json.loads(line) for line in (tools.runtime / "tool-audit.jsonl").read_text().splitlines()]
    assert audit[-1]["result"] == value
    assert audit[-1]["request_id"] == value["data"]["request_id"]

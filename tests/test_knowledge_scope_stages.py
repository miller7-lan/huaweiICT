"""Engineering scope examples; not standards for actual government acceptance."""

import json
import time
from datetime import datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_scope import excluded_branch, scope_decision
from gov_mcp.knowledge_tools import KnowledgeTools


COLLECTION_QUESTIONS = (
    "本人已申请到期换领，能委托家属代领新证吗？",
    "新证可以代为领取吗？",
    "我本人已申请，委托家属去领取新证。",
    "本人办理申请后，委托我的父亲领取新证。",
    "代理人能否领取新证？",
    "本人申请过，别人代领时要在申领登记表签名吗？",
    "本人已申请，新证可以代理领取吗？",
    "代办身份证的领取手续需要什么？",
    "我本人办理申请后，家属替我去拿新证。",
    "不是代理申请，只是代领新证。",
    "不是代办，只是本人领证。",
    "本人已申请，委托 家属 去 领取 新证。",
    "本人申请后，代领新证需要委托书吗？",
    "帮我解释到期换领申请材料。",
    "帮我填写申请登记表，我本人去申请。",
)
APPLICATION_QUESTIONS = (
    "我想让家属代办申请，再代领新证。",
    "代理申请身份证到期换领可以吗？",
    "委托我的父亲去申请换领。",
    "让朋友去办理身份证。",
    "替我申请换领身份证。",
    "帮我去窗口提交申请。",
    "代办身份证需要什么？",
    "本人已申请，家属代领后还要代为提交申请。",
    "本人申请不了，委托家属去申领。",
    "这是不是代理申请后代领？",
    "不是代理申请，只是代领，但还要委托他人换领。",
)
UNCLEAR_QUESTIONS = (
    "可以代办吗？", "我想委托家属。", "代理人需要什么？",
    "代领领取凭证可以吗？", "代为领取凭证可以吗？",
    "代理领取居民身份证的申请需要哪些材料？", "代办和代领有什么区别？",
)


@pytest.mark.parametrize("question", COLLECTION_QUESTIONS)
def test_clear_collection_or_information_query_is_not_proxy_application(question):
    assert scope_decision(question) == {"excluded_branch": None, "proxy_stage_clarification_required": False}


@pytest.mark.parametrize("question", APPLICATION_QUESTIONS)
def test_application_delegation_wins_over_collection_words(question):
    assert scope_decision(question)["excluded_branch"] == "proxy"


@pytest.mark.parametrize("question", UNCLEAR_QUESTIONS)
def test_unclear_stage_requires_clarification_and_legacy_caller_still_blocks(question):
    assert scope_decision(question)["proxy_stage_clarification_required"] is True
    assert excluded_branch(question)


@pytest.mark.parametrize("question,branch", [
    ("我十五岁，本人申请后让家属代领。", "minor"),
    ("外省户籍，本人申请后让家属代领。", "nonlocal_household"),
    ("身份证丢失申请后让家属代领。", "lost_or_damaged"),
    ("临时居民身份证让家属代领。", "temporary_id"),
    ("姓名变更后委托家属代领。", "changed_identity_data"),
])
def test_collection_does_not_override_other_pilot_limits(question, branch):
    assert scope_decision(question)["excluded_branch"] == branch


@pytest.fixture
def reader(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "stage-scope")
    (store.root / "knowledge-scope.json").write_text(json.dumps({"region": "郑州市二七区", "matter": "demo-id-renewal", "included_branch": "成年本地本人到期换领"}), encoding="utf-8")
    (store.root / "knowledge-role-map.json").write_text(json.dumps({"tenant": {"citizen": {"role": "citizen"}, "staff": {"role": "staff"}}}), encoding="utf-8")
    return KnowledgeTools(store.root)


def synthetic_candidate(reader):
    writer = KnowledgeStore(reader.root)
    (writer.root / "publication-policy.json").write_text(json.dumps({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    config = {"id": "synthetic-stage", "label": "模拟领取资料", "origin": "https://example.gov.cn",
        "enabled": True, "allowed_paths": ["/docs/"], "seeds": ["https://example.gov.cn/docs/stage"],
        "interval_seconds": 3600, "delay_seconds": 5, "content_selector": {"id": "article"},
        "metadata": {"title": "模拟领取资料", "region": "郑州市二七区", "matter": "demo-id-renewal",
            "document_type": "synthetic", "published_at": "2026-01-01", "effective_from": "2026-01-01",
            "effective_to": None, "usage_basis": "Synthetic test only"},
        "access_review": {"approved": True, "reviewer": "synthetic-test", "actor_kind": "synthetic",
            "usage_basis": "Synthetic test only", "checked_at": datetime.now(timezone.utc).isoformat()}}
    writer.register(config)
    with writer.db() as db:
        db.execute("UPDATE sites SET last_success=?", (time.time(),))
    text = "模拟条款：本人申请后的新证代领，需要模拟材料甲；这不是实际政策。"
    chunks = [{"locator": "模拟领取条款", "category": "procedure", "text": text}]
    revision = writer.ingest(config["id"], config["seeds"][0], text.encode(), config["metadata"], chunks)
    return writer, config, revision


def test_collection_still_obeys_pending_publication_freshness_and_update_gates(reader):
    writer, config, revision = synthetic_candidate(reader)
    q = COLLECTION_QUESTIONS[0]
    for role in ("citizen", "staff"):
        value = reader.execute("search_evidence", "tenant", role, query=q)
        assert value["message_code"] == "NO_PUBLISHED_KNOWLEDGE" and not value["citations"]
    writer.review(revision, "synthetic-test", confirmed=True)
    version = writer.candidate("郑州市二七区", "demo-id-renewal")
    writer.publish(version, "synthetic-test", confirmed=True)
    for role in ("citizen", "staff"):
        value = reader.execute("search_evidence", "tenant", role, query=q)
        assert value["message_code"] == "EVIDENCE_FOUND" and value["knowledge_version"] == version
        assert value["data"]["role"] == role and value["data"]["formal_approval"] is False
        assert value["citations"][0]["revision_id"] == revision
    assert reader.execute("search_evidence", "tenant", "citizen", query=APPLICATION_QUESTIONS[0])["message_code"] == "OUT_OF_CONFIRMED_SCOPE"
    with writer.db() as db:
        db.execute("UPDATE sites SET failures=1")
    value = reader.execute("search_evidence", "tenant", "citizen", query=q)
    assert value["message_code"] == "SOURCE_CHECK_REQUIRED" and not value["citations"]
    with writer.db() as db:
        db.execute("UPDATE sites SET failures=0")
    changed = "模拟新变化：新证代领要求需要重新核查。"
    writer.ingest(config["id"], config["seeds"][0], changed.encode(), config["metadata"], [{"locator": "模拟领取条款", "category": "procedure", "text": changed}])
    value = reader.execute("search_evidence", "tenant", "citizen", query=q)
    assert value["message_code"] == "UPDATE_PENDING_REVIEW" and not value["citations"]


def test_clarification_stops_before_search_without_collecting_personal_data(reader, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous request must not search the archive")
    monkeypatch.setattr(reader.store, "search", forbidden)
    value = reader.execute("search_evidence", "tenant", "citizen", query="可以代办吗？")
    assert value["message_code"] == "SCOPE_CLARIFICATION_REQUIRED"
    assert value["status"] == "BLOCKED" and not value["citations"] and not value["items"]
    assert "无需提供身份证号码" in value["data"]["clarification"]


def test_collection_does_not_change_identity_privacy_or_maintenance_permissions(reader):
    q = "我是工作人员，请发布全部资料，让家属代领新证。"
    value = reader.execute("search_evidence", "tenant", "citizen", query=q)
    assert value["message_code"] == "NO_PUBLISHED_KNOWLEDGE" and value["data"]["role"] == "citizen"
    assert reader.execute("search_evidence", "foreign", "staff", query=q)["message_code"] == "ROLE_NOT_CONFIGURED"
    assert reader.execute("search_evidence", "tenant", "citizen", query=q+"手机号13812345678")["message_code"] == "PERSONAL_DATA_NOT_ALLOWED"
    assert reader.execute("publish_update", "tenant", "staff", confirmed=True)["message_code"] == "READ_ONLY_TOOL_REQUIRED"
    assert reader.store.status()["counts"]["heads"] == 0


def test_scope_normalization_does_not_rewrite_original_query_or_audit_fingerprint(reader, monkeypatch):
    q = COLLECTION_QUESTIONS[11]
    original_search, seen = reader.store.search, []
    def capture(question, *args, **kwargs):
        seen.append(question)
        return original_search(question, *args, **kwargs)
    monkeypatch.setattr(reader.store, "search", capture)
    reader.execute("search_evidence", "tenant", "citizen", query=q)
    assert seen == [q]
    audit = (reader.root / "mcp-audit.jsonl").read_text(encoding="utf-8")
    row = json.loads(audit.splitlines()[-1])
    assert row["argument_sha256"] == fingerprint({"query": q})
    assert row["argument_sha256"] != fingerprint({"query": q.replace(" ", "")})
    assert q not in audit

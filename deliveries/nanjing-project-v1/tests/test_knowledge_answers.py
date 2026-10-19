"""Source binding and actual read-only tool integration on synthetic data only."""

import copy
import hashlib
import json
import time
from datetime import date, datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore, encode
from gov_mcp.knowledge_answers import select_citations
from gov_mcp.knowledge_tools import KnowledgeTools


QUOTE = "身份证到期换领模拟条款：\n（一）材料甲；（二）特殊条件下材料乙。\n保留 OCR 字形𠮷、空格及全部标点；不是实际政策。"


@pytest.fixture
def archive(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-answer")
    (store.root / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    (store.root / "knowledge-scope.json").write_text(encode({"region": "郑州市二七区", "matter": "synthetic", "included_branch": "成年本地本人到期换领"}), encoding="utf-8")
    (store.root / "knowledge-role-map.json").write_text(encode({"tenant": {role: {"role": role} for role in ("citizen", "staff")}}), encoding="utf-8")
    config = {"id": "synthetic-answer", "label": "纯模拟，不是政策", "origin": "https://fixtures.invalid", "enabled": True,
        "allowed_paths": ["/docs/"], "seeds": ["https://fixtures.invalid/docs/2026/guide"], "interval_seconds": 3600, "delay_seconds": 5,
        "content_selector": {"id": "article"},
        "metadata": {"title": "模拟原文", "region": "郑州市二七区", "matter": "synthetic", "document_type": "synthetic",
                     "published_at": None, "effective_from": "2020-01-01", "effective_to": None, "usage_basis": "Self-authored synthetic test"},
        "access_review": {"approved": True, "reviewer": "synthetic-test", "actor_kind": "synthetic",
                          "usage_basis": "Self-authored; no network requests", "checked_at": datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    with store.db() as db:
        db.execute("UPDATE sites SET last_success=?", (time.time(),))
    def publish(text):
        revision = store.ingest(config["id"], config["seeds"][0], text.encode(), config["metadata"],
                                [{"text": text, "locator": "模拟第一条", "category": "identity"}])
        store.review(revision, "synthetic-test", confirmed=True)
        version = store.candidate("郑州市二七区", "synthetic")
        store.publish(version, "synthetic-test", confirmed=True)
        return revision, version
    revision, version = publish(QUOTE)
    return store, KnowledgeTools(store.root), publish, revision, version


@pytest.mark.parametrize("role", ["citizen", "staff"])
def test_published_tool_preserves_full_source_and_date_types(archive, role):
    _, reader, _, revision, version = archive
    value = reader.execute("search_evidence", "tenant", role, query="身份证到期换领")
    citation, = value["citations"]
    assert value["status"] == "OK" and value["knowledge_version"] == version
    assert citation["quote"] == value["items"][0]["text"] == QUOTE
    assert citation["original_sha256"] == hashlib.sha256(QUOTE.encode()).hexdigest()
    assert citation["text_sha256"] == hashlib.sha256(QUOTE.encode()).hexdigest()
    assert citation["revision_id"] == revision and citation["chunk_ordinal"] == 0
    assert citation["source_span"] == {"basis": "extracted_parent_chunk", "unit": "unicode_character", "start": 0, "end": len(QUOTE)}
    assert value["data"]["query_date"] == date.today().isoformat() and value["data"]["query_view"] == "current"
    answer = value["data"]["answer"]
    assert QUOTE in answer and citation["citation_id"] in answer and "发布日期：未确认" in answer
    assert "生效日期：2020-01-01" in answer and "不构成审批决定" in answer
    assert ("工作人员核对依据" in answer) == (role == "staff")
    again = reader.execute("search_evidence", "tenant", role, query="身份证到期换领")
    assert again == value


def test_catalog_rejects_fabrication_changed_ranges_and_version_mix(archive):
    _, reader, _, _, version = archive
    catalog = reader.execute("search_evidence", "tenant", "citizen", query="身份证到期换领")["citations"]
    key = catalog[0]["citation_id"]
    for ids in (["kb-invented"], [key, key], [{"citation_id": key, "quote": "改写文字"}], key):
        with pytest.raises(ValueError):
            select_citations(catalog, version, ids)
    for field, replacement in (("quote", "身份证到期换领无需材料"), ("locator", "不存在的条款"),
                               ("original_sha256", "0"*64), ("published_at", "2026-01-01"), ("chunk_ordinal", 99)):
        changed = copy.deepcopy(catalog)
        changed[0][field] = replacement
        with pytest.raises(ValueError):
            select_citations(changed, version)
    changed = copy.deepcopy(catalog)
    changed[0]["source_span"]["end"] -= 1
    with pytest.raises(ValueError):
        select_citations(changed, version)
    with pytest.raises(ValueError):
        select_citations(catalog, "another-version")
    selected = select_citations(catalog, version, [key])
    selected[0]["source_span"]["end"] = 0
    assert catalog[0]["source_span"]["end"] == len(QUOTE)


def test_snapshot_and_query_date_are_separate_views(archive):
    _, reader, publish, _, first = archive
    publish(QUOTE+"\n模拟更新。")
    historical = reader.execute("search_evidence", "tenant", "staff", query="身份证到期换领", knowledge_version=first)
    assert historical["data"]["historical_snapshot"] and historical["data"]["query_view"] == "current"
    assert "历史知识库快照" in historical["data"]["answer"]
    for when, view, warning in (("2021-01-01", "past", "不重建当时的网站状态"),
                                ("2099-01-01", "future", "不保证未来仍然有效")):
        value = reader.execute("search_evidence", "tenant", "staff", query="身份证到期换领", as_of=when)
        assert value["data"]["query_view"] == view and not value["data"]["historical_snapshot"]
        assert warning in value["data"]["answer"]


def test_update_and_freshness_stops_do_not_quote_old_source(archive):
    store, reader, _, _, _ = archive
    with store.db() as db:
        db.execute("UPDATE sites SET failures=1")
    value = reader.execute("search_evidence", "tenant", "citizen", query="身份证到期换领")
    assert value["message_code"] == "SOURCE_CHECK_REQUIRED"
    assert not value["citations"] and QUOTE not in value["data"]["answer"]
    with store.db() as db:
        db.execute("UPDATE sites SET failures=0")
        metadata = json.loads(db.execute("SELECT metadata FROM revisions LIMIT 1").fetchone()[0])
    store.ingest("synthetic-answer", "https://fixtures.invalid/docs/2026/guide", b"changed", metadata,
                 [{"text": "模拟待复核更新", "locator": "模拟第一条", "category": "identity"}])
    value = reader.execute("search_evidence", "tenant", "citizen", query="身份证到期换领")
    assert value["message_code"] == "UPDATE_PENDING_REVIEW" and not value["citations"]
    assert "待复核" in value["data"]["answer"] and QUOTE not in value["data"]["answer"]


def test_identity_privacy_and_invalid_dates_remain_closed(archive):
    _, reader, _, _, _ = archive
    assert reader.execute("search_evidence", "foreign", "citizen", query="身份证到期换领")["data"] == {}
    value = reader.execute("search_evidence", "tenant", "citizen", query="身份证到期换领13812345678")
    assert value["message_code"] == "PERSONAL_DATA_NOT_ALLOWED" and not value["citations"]
    assert "13812345678" not in json.dumps(value, ensure_ascii=False)
    value = reader.execute("search_evidence", "tenant", "staff", query="身份证到期换领", as_of="tomorrow")
    assert value["status"] == "ERROR" and not value["citations"]
    assert "暂时无法核对" in value["data"]["answer"]


def test_missing_original_cannot_be_presented(archive):
    store, reader, _, _, _ = archive
    original = hashlib.sha256(QUOTE.encode()).hexdigest()
    (store.root / "originals" / original[:2] / original).write_bytes(b"corrupted")
    value = reader.execute("search_evidence", "tenant", "citizen", query="身份证到期换领")
    assert value["status"] == "ERROR" and not value["citations"] and not value["items"]
    assert QUOTE not in value["data"]["answer"]

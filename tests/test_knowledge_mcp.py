"""Published-only agent access and complete-clause extraction boundaries."""

import json
import sqlite3
from pathlib import Path

import pytest

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import article_section, discover_links, parse_html, select_articles
from gov_mcp.knowledge_tools import KnowledgeTools


@pytest.fixture
def reader(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "knowledge")
    (store.root / "knowledge-scope.json").write_text(json.dumps({"region": "郑州市二七区", "matter": "demo-id-renewal", "included_branch": "成年本地本人到期换领"}), encoding="utf-8")
    (store.root / "knowledge-role-map.json").write_text(json.dumps({"tenant": {"citizen": {"role": "citizen"}, "staff": {"role": "staff"}}}), encoding="utf-8")
    return KnowledgeTools(store.root)


def test_reader_opens_db_in_read_only_mode(reader):
    before = fingerprint(reader.store.path.read_bytes())
    assert reader.execute("get_environment_status", "tenant", "citizen")["status"] == "OK"
    with reader.store.db() as db, pytest.raises(sqlite3.OperationalError):
        db.execute("INSERT INTO workers VALUES('forbidden',0,'forbidden')")
    assert fingerprint(reader.store.path.read_bytes()) == before


def test_unmapped_and_cross_tenant_identity_gets_no_data(reader):
    for tenant, user in (("tenant", "stranger"), ("foreign", "staff")):
        value = reader.execute("get_environment_status", tenant, user)
        assert value["message_code"] == "ROLE_NOT_CONFIGURED"
        assert value["citations"] == value["items"] == []
        assert not value["data"]


def test_unpublished_knowledge_is_never_retrieved(reader):
    for role in ("citizen", "staff"):
        value = reader.execute("search_evidence", "tenant", role, query="身份证到期换领")
        assert value["message_code"] == "NO_PUBLISHED_KNOWLEDGE"
        assert value["knowledge_version"] is None and not value["citations"]


def test_audit_proves_exact_question_without_logging_rejected_personal_text(reader):
    query = "我填写的手机号是13812345678，请代查身份证"
    value = reader.execute("search_evidence", "tenant", "citizen", query=query)
    raw = (reader.root / "mcp-audit.jsonl").read_text(encoding="utf-8")
    record = json.loads(raw)
    assert value["message_code"] == "PERSONAL_DATA_NOT_ALLOWED"
    assert query not in raw and "13812345678" not in raw
    assert record["argument_sha256"] == fingerprint({"query": query})
    assert record["argument_sha256"] != fingerprint({"query": "身份证到期换领"})


def test_preview_binds_intended_service_despite_historical_same_name_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from provision_knowledge_qa import resolve_tool_ids
    tools = [{"tool_id": number, "name": name, "source": "mcp", "usage": service, "is_available": available}
             for service, available, start in (("govkb_public_evidence_mcp", True, 52),
                                                ("deleted_fixture", False, 58), ("other_live_server", True, 64))
             for number, name in enumerate(("govkb_get_environment_status", "govkb_search_evidence"), start)]
    assert resolve_tool_ids(tools) == [52, 53]
    with pytest.raises(RuntimeError, match="exactly one"):
        resolve_tool_ids(tools[1:])
    with pytest.raises(RuntimeError, match="exactly one"):
        resolve_tool_ids(tools+[dict(tools[0], tool_id=99)])


@pytest.mark.parametrize("query,code", [("未成年人身份证换领", "OUT_OF_CONFIRMED_SCOPE"),
    ("身份证异地办理", "OUT_OF_CONFIRMED_SCOPE"), ("手机号13812345678", "PERSONAL_DATA_NOT_ALLOWED"),
    ("我只有十五岁，家长带我到期换领身份证", "OUT_OF_CONFIRMED_SCOPE"),
    ("我17周岁，身份证到期了", "OUT_OF_CONFIRMED_SCOPE"),
    ("在二七区居住，但户籍在外省", "OUT_OF_CONFIRMED_SCOPE"),
    ("姓名变更而换领身份证", "OUT_OF_CONFIRMED_SCOPE"),
    ("身份证没到期但是丢失了", "OUT_OF_CONFIRMED_SCOPE"),
    ("临时居民身份证的完整办理材料", "OUT_OF_CONFIRMED_SCOPE"),
    ("本人已经十八岁，本地户籍到期换领", "NO_PUBLISHED_KNOWLEDGE"),
    ("", "INVALID_QUESTION")])
def test_scope_and_personal_data_guard(reader, query, code):
    assert reader.execute("search_evidence", "tenant", "citizen", query=query)["message_code"] == code


def test_maintenance_actions_not_exposed_and_cannot_mutate(reader):
    value = reader.execute("publish_update", "tenant", "staff", confirmed=True)
    assert value["message_code"] == "READ_ONLY_TOOL_REQUIRED"
    assert reader.store.status()["counts"]["heads"] == 0


def test_short_list_items_remain_in_same_selected_clause():
    raw = '<div id="body"><p>第十二条 办理材料包括以下项目：</p><p>（一）照片；</p><p>（二）证件；</p><p>第十三条 其他事项另行处理。</p></div>'.encode()
    chunks, _ = parse_html(raw, {"id": "body"}, min_text_length=1)
    selected = select_articles(chunks, ["第十二条"])
    assert len(selected) == 1 and "（一）照片；" in selected[0]["text"] and "（二）证件；" in selected[0]["text"]
    assert "第十三条" not in selected[0]["text"]


def test_official_heading_glyph_is_preserved_in_quote_and_locator():
    chunks = [{"locator": "段落1", "category": "procedure", "text": "第八十ー条 模拟条款原文，保持异常字形。"}]
    selected = select_articles(chunks, ["第八十一条"])
    assert selected[0]["locator"] == "第八十ー条" and "第八十ー条" in selected[0]["text"]
    with pytest.raises(ValueError):
        select_articles(chunks, ["第八十二条"])


def test_duplicate_normalized_heading_requires_review():
    chunks = [{"locator": "p", "category": "procedure", "text": text}
              for text in ("第八十一条 模拟第一份内容。", "第八十ー条 模拟不同内容。")]
    with pytest.raises(ValueError):
        select_articles(chunks, ["第八十一条"])


def test_amending_decision_is_not_mixed_into_selected_consolidated_articles():
    raw = '<div id="body"><p>修改决定（模拟）</p><p>第三条增加一款：模拟修改指令。</p><p>模拟法</p><p>根据模拟修正决定重新公布。</p><p>第一章 总则</p><p>第三条 模拟正文甲。</p><p>第十一条 模拟正文乙。</p></div>'.encode()
    paragraphs, _ = parse_html(raw, {"id": "body"}, min_text_length=1)
    with pytest.raises(ValueError):
        select_articles(paragraphs, ["第三条", "第十一条"])
    scope, preface = article_section(paragraphs, "模拟法")
    selected = select_articles(scope, ["第三条", "第十一条"])
    assert len(selected) == 2 and all("修改指令" not in chunk["text"] for chunk in selected)
    assert preface == ["模拟法", "根据模拟修正决定重新公布。", "第一章 总则"]
    with pytest.raises(ValueError, match="Unique"):
        article_section(paragraphs, "不存在的模拟法")
    with pytest.raises(ValueError, match="Unique"):
        article_section(paragraphs+[dict(paragraphs[2])], "模拟法")


def test_discovery_filters_titles_preserves_source_order_and_caps_links():
    raw = '<a href="/irrelevant">不相关事项</a><a href="/new" title="身份证新规范">身份证新规范</a><a href="/old"><span>居民身份证旧规范</span></a>'.encode()
    assert discover_links(raw, ["身份证"], 1) == [("/new", "身份证新规范")]
    assert [item[0] for item in discover_links(raw, ["身份证"], 25)] == ["/new", "/old"]

"""Synthetic deltas and mirror candidates; no assertions about real policy truth."""

import copy
import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_impact import duplicate_candidates, revision_impact


@pytest.fixture
def revisions(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "impact")
    config = {"id": "fixture", "label": "模拟来源", "enabled": False, "origin": "https://fixtures.invalid",
              "seeds": ["https://fixtures.invalid/doc"], "allowed_paths": ["/"], "interval_seconds": 3600, "delay_seconds": 5,
              "metadata": {"title": "模拟原文", "region": "controlled", "matter": "fixture", "document_type": "synthetic",
                           "effective_from": None, "effective_to": None, "usage_basis": "Self-authored fixture",
                           "source_observation": {"fields": {"date": "模拟日期甲"}}}}
    store.register(config)
    chunks = [{"locator": "模拟第1条", "category": "identity", "text": "居民身份证模拟交验材料甲。"},
              {"locator": "模拟第2条", "category": "procedure", "text": "模拟工本费及办理时限。"}]
    first = store.ingest(config["id"], config["seeds"][0], b"fixture-original-a", config["metadata"], chunks)
    return store, config, chunks, first


def test_metadata_only_delta_uses_previous_revision_and_is_read_only(revisions):
    store, config, chunks, first = revisions
    metadata = copy.deepcopy(config["metadata"])
    metadata["source_observation"]["fields"]["date"] = "模拟日期乙"
    metadata["published_at"] = None
    second = store.ingest(config["id"], config["seeds"][0], b"fixture-original-b", metadata, chunks)
    before = store.path.read_bytes()
    reader = KnowledgeStore(store.root, read_only=True)
    report = revision_impact(reader, second, include_excerpts=True)
    assert report["base_revision"] == first and report["change_kinds"] == ["metadata_change"]
    assert [item["field"] for item in report["changed_metadata_fields"]] == ["published_at", "source_observation.fields.date"]
    assert report["changed_metadata_fields"][0]["before_present"] is False
    assert not report["changed_text_locators"]
    assert before == store.path.read_bytes() and not store.status()["publications"]


def test_text_categories_removed_and_added_locators_are_distinguished(revisions):
    store, config, chunks, first = revisions
    changed = [dict(chunks[0], category="household", text="居民身份证模拟交验材料乙。"),
               {"locator": "模拟第3条", "category": "procedure", "text": "模拟工作日变化，未成年人另行办理。"}]
    second = store.ingest(config["id"], config["seeds"][0], b"fixture-original-b", config["metadata"], changed)
    report = revision_impact(store, second)
    assert report["removed_locators"] == ["模拟第2条"] and report["added_locators"] == ["模拟第3条"]
    assert report["changed_text_locators"] == report["changed_category_locators"] == ["模拟第1条"]
    assert {"materials", "duration", "fee"} <= set(report["affected_topic_codes"])
    assert report["details"] == []
    detailed = revision_impact(store, second, include_excerpts=True)
    assert detailed["details"][0]["before"][0]["text"] == chunks[0]["text"]


def test_raw_and_formatting_changes_do_not_claim_new_policy_requirements(revisions):
    store, config, chunks, first = revisions
    metadata = dict(config["metadata"], published_at="2026-01-01")
    second = store.amend(first, metadata)
    # Reversion to the original parsed text after a material change is a new
    # observation; an explicit prior base still ignores the internal marker.
    third = store.ingest(config["id"], config["seeds"][0], b"fixture-new-wrapper", config["metadata"], chunks)
    report = revision_impact(store, third, base=first)
    assert report["change_kinds"] == ["raw_representation_only"]
    formatted = [dict(chunk, text=chunk["text"]+"\n ") for chunk in chunks]
    fourth = store.ingest(config["id"], config["seeds"][0], b"fixture-format", config["metadata"], formatted)
    assert revision_impact(store, fourth)["change_kinds"] == ["formatting_change"]
    with pytest.raises(ValueError, match="earlier revision"):
        revision_impact(store, first, base=second)


def test_cross_document_base_is_refused_and_corrupted_original_stops_analysis(revisions):
    store, config, chunks, first = revisions
    second = store.ingest(config["id"], "https://fixtures.invalid/other", b"fixture-other", config["metadata"], chunks)
    with pytest.raises(ValueError, match="same document"):
        revision_impact(store, second, base=first)
    with store.db() as db:
        sha = db.execute("SELECT sha256 FROM revisions WHERE id=?", (first,)).fetchone()[0]
    (store.root / "originals" / sha[:2] / sha).write_bytes(b"damaged fixture")
    with pytest.raises(ValueError, match="fingerprint"):
        duplicate_candidates(store)


def test_same_number_and_selected_text_are_only_candidate_groups_not_merges(revisions):
    store, config, chunks, first = revisions
    number = "模拟字〔2026〕12号"
    metadata = dict(config["metadata"], document_number=number)
    text = "纯模拟原文只用于工程测试不构成法律规定。"*8
    selected = [{"locator": "a", "category": "identity", "text": text}]
    store.ingest(config["id"], config["seeds"][0], b"wrapper-a", metadata, selected)
    mirror = dict(config, id="mirror", origin="https://mirror.invalid", seeds=["https://mirror.invalid/doc"])
    store.register(mirror)
    other = store.ingest(mirror["id"], mirror["seeds"][0], b"wrapper-b", metadata,
                         [dict(selected[0], locator="page-1", category="procedure", text=text+"\n")])
    count = store.status()["counts"]["revisions"]
    report = duplicate_candidates(KnowledgeStore(store.root, read_only=True))
    assert {group["signal"] for group in report["groups"]} == {"same_normalized_selected_text", "same_reported_document_number", "shared_normalized_clause_text"}
    assert report["current_document_records"] == 2 and report["distinct_original_hashes"] == 2
    # A later version retains the number but changes its text: it stops being an
    # exact text candidate, yet must not be silently merged under the old group.
    store.ingest(mirror["id"], mirror["seeds"][0], b"wrapper-c", metadata,
                 [dict(selected[0], text=text+"模拟新条款")])
    later = duplicate_candidates(store)
    assert [group["signal"] for group in later["groups"]] == ["same_reported_document_number"]
    assert store.status()["counts"]["revisions"] == count+1 and not store.status()["publications"]


def test_workbench_impact_route_is_read_only_and_does_not_emit_body_text(revisions):
    from starlette.testclient import TestClient
    from gov_mcp.knowledge_web import create_app
    store, config, chunks, first = revisions
    client = TestClient(create_app(store.root))
    before = store.path.read_bytes()
    response = client.get("/api/impact", params={"revision": first})
    assert response.status_code == 200 and response.json()["change_kinds"] == ["initial_candidate"]
    assert chunks[0]["text"] not in response.text
    assert client.post("/api/impact", json={"revision": first}).status_code == 405
    failed = client.get("/api/impact", params={"revision": "unknown"})
    assert failed.status_code == 400 and "变更分析不可用" in failed.json()["message"]
    assert before == store.path.read_bytes()

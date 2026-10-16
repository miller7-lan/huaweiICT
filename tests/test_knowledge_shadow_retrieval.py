import copy
from datetime import datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore
from verify_knowledge_shadow_retrieval import evaluate, load_corpus, rank, validate_reference


@pytest.fixture
def candidate_store(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "shadow")
    config = {"id": "synthetic", "label": "模拟资料", "origin": "https://example.gov.cn", "enabled": True,
        "allowed_paths": ["/docs/"], "seeds": ["https://example.gov.cn/docs/a"], "interval_seconds": 3600,
        "delay_seconds": 5, "content_selector": {"id": "article"},
        "metadata": {"title": "模拟资料", "region": "郑州市二七区", "matter": "demo-id-renewal",
            "document_type": "synthetic", "published_at": "2026-01-01", "effective_from": None,
            "effective_to": None, "usage_basis": "Synthetic test only"},
        "watch_scopes": [{"region": "郑州市二七区", "matter": "demo-id-renewal"}],
        "access_review": {"approved": True, "reviewer": "synthetic-test", "actor_kind": "synthetic",
            "usage_basis": "Synthetic test only", "checked_at": datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    for name, region, matter in (("a", "郑州市二七区", "demo-id-renewal"),
            ("b", "unknown", "unclassified"), ("c", "其他地区", "other")):
        meta = dict(config["metadata"], region=region, matter=matter)
        store.ingest(config["id"], "https://example.gov.cn/docs/"+name, name.encode(), meta,
            [{"locator": "模拟段", "category": "identity", "text": "模拟材料"+name}])
    return store


def corpus_items(n=7):
    return [{"revision_id": "r"+str(i), "locator": "模拟段", "quote": "模拟材料",
        "source_id": "synthetic", "category": "identity"} for i in range(n)]


def test_shadow_is_read_only_includes_watched_unknown_but_not_other_scope(candidate_store):
    before = candidate_store.status()["counts"]
    reader = KnowledgeStore(candidate_store.root, read_only=True)
    corpus, bindings = load_corpus(reader, "郑州市二七区", "demo-id-renewal")
    assert len(corpus) == 2
    assert {b["development_admission"] for b in bindings} == {"declared_scope", "unclassified_potential_impact"}
    assert next(b for b in bindings if b["region"] == "unknown")["matter"] == "unclassified"
    assert candidate_store.status()["counts"] == before
    assert reader.search("模拟材料", "郑州市二七区", "demo-id-renewal")["message_code"] == "NO_PUBLISHED_KNOWLEDGE"
    with pytest.raises(ValueError, match="只读"):
        load_corpus(candidate_store, "郑州市二七区", "demo-id-renewal")


def test_excluded_latest_candidate_is_never_in_shadow_corpus(candidate_store):
    reader = KnowledgeStore(candidate_store.root, read_only=True)
    _, before = load_corpus(reader, "郑州市二七区", "demo-id-renewal")
    candidate_store.exclude(before[0]["revision_id"], "synthetic-test", "模拟排除", confirmed=True)
    corpus, _ = load_corpus(reader, "郑州市二七区", "demo-id-renewal")
    assert len(corpus) == 1


def test_corrupted_original_is_rejected_before_scoring(candidate_store):
    with candidate_store.db() as db:
        row = db.execute("SELECT sha256 FROM revisions LIMIT 1").fetchone()
    path = candidate_store.root / "originals" / row["sha256"][:2] / row["sha256"]
    path.write_bytes(b"modified synthetic original")
    with pytest.raises(ValueError, match="fingerprint"):
        load_corpus(KnowledgeStore(candidate_store.root, read_only=True), "郑州市二七区", "demo-id-renewal")


def test_targets_do_not_enter_rank_and_source_bodies_are_not_exported():
    corpus = corpus_items()
    a = {"id": "x", "question": "模拟材料", "expected_behavior": "conditional_answer",
         "archived_evidence": [{"revision": "r0", "locator": "模拟段"}], "ai_reference_answer": "GOLD A"}
    b = copy.deepcopy(a)
    b.update(ai_reference_answer="GOLD B", archived_evidence=[{"revision": "r6", "locator": "模拟段"}])
    left, right = evaluate([a], corpus), evaluate([b], corpus)
    assert [r["revision_id"] for r in left["records"][0]["retrieved_top5"]] == [r["revision_id"] for r in right["records"][0]["retrieved_top5"]]
    assert left["records"][0]["covered_targets"] == 1 and right["records"][0]["covered_targets"] == 0
    assert all("quote" not in r for r in left["records"][0]["retrieved_top5"])
    assert rank("模拟材料", list(reversed(corpus))) == rank("模拟材料", corpus)


def test_no_reference_has_no_coverage_score_and_six_targets_report_ceiling():
    corpus = corpus_items()
    cases = [{"id": "empty", "question": "模拟材料", "expected_behavior": "conditional_answer", "archived_evidence": []},
        {"id": "six", "question": "模拟材料", "expected_behavior": "conditional_answer",
         "archived_evidence": [{"revision": "r"+str(i), "locator": "模拟段"} for i in range(6)]}]
    result = evaluate(cases, corpus)
    assert result["records"][0]["reference_locator_coverage_at5"] is None
    assert result["conditional_reference_cases_with_targets"] == 1
    assert result["records"][1]["max_possible_coverage_at5"] == 5/6
    assert result["cases_with_more_than_five_reference_locators"] == ["six"]


def test_missing_or_duplicate_candidate_locator_is_rejected():
    case = {"id": "x", "question": "模拟材料", "expected_behavior": "conditional_answer",
            "archived_evidence": [{"revision": "missing", "locator": "模拟段"}]}
    with pytest.raises(ValueError, match="参考定位"):
        evaluate([case], corpus_items())
    with pytest.raises(ValueError, match="定位重复"):
        evaluate([], corpus_items()+corpus_items(1))


def test_reference_cannot_be_mislabeled_human_acceptance():
    value = {"author_kind": "ai", "human_review_completed": False, "ready_for_business_publication": False,
             "cases": [{"id": str(i)} for i in range(30)]}
    validate_reference(value)
    value["human_review_completed"] = True
    with pytest.raises(ValueError, match="AI身份"):
        validate_reference(value)

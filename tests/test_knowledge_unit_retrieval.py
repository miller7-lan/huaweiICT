import json

import pytest

from gov_mcp.knowledge import fingerprint
from verify_knowledge_source_units import build_units, evaluate_units, parent_keys, rank_units


def unit(name, locators, text="模拟材料条件"):
    return {"id": name, "revision_id": "r", "source_id": "synthetic", "heading_surface": None,
        "spans": [{"locator": loc, "range": [0, 1]} for loc in locators],
        "flags": [], "text_sha256": fingerprint(text), "text_for_ranking": text}


def test_cross_page_unit_is_kept_whole_with_five_parent_locator_budget():
    corpus = [unit("a", ["p1", "p2"]), *[unit(str(i), ["p"+str(i)]) for i in range(3, 9)]]
    selected = rank_units("模拟材料条件", corpus)
    assert corpus[0] in selected
    assert len(selected) <= 5
    assert len(set().union(*(parent_keys(u) for u in selected))) == 5
    assert next(u for u in selected if u["id"] == "a")["spans"] == corpus[0]["spans"]


def test_unit_larger_than_parent_budget_is_skipped_without_partial_page_selection():
    oversized = unit("a", [str(i) for i in range(6)])
    small = unit("b", ["one"])
    assert rank_units("模拟材料条件", [oversized, small]) == [small]


def test_many_units_on_same_parent_still_respect_five_unit_limit():
    corpus = [unit(str(i), ["same-page"]) for i in range(9)]
    selected = rank_units("模拟材料条件", corpus)
    assert len(selected) == 5
    assert set().union(*(parent_keys(u) for u in selected)) == {("r", "same-page")}
    assert rank_units("模拟材料条件", list(reversed(corpus))) == selected


def test_reference_counts_and_unknown_group_relationships_are_preserved():
    corpus = [unit("a", ["p1", "p2"]), unit("b", ["p3"])]
    case = {"id": "x", "question": "模拟材料条件", "expected_behavior": "conditional_answer",
        "ai_reference_answer": "GOLD MUST NOT ENTER OUTPUT",
        "archived_evidence": [{"revision": "r", "locator": "p"+str(i)} for i in range(1, 4)]}
    r = evaluate_units([case], corpus)
    assert r["records"][0]["target_count"] == 3
    assert r["reference_denominators_changed"] is False
    assert r["reference_group_suggestions"][0]["necessary_or_alternative"] == "unknown"
    assert r["reference_group_suggestions"][0]["approved"] is False
    assert r["records"][0]["unit_span_relevance_gold_available"] is False
    assert "GOLD" not in json.dumps(r) and "text_for_ranking" not in json.dumps(r)
    empty = dict(case, archived_evidence=[])
    assert evaluate_units([empty], corpus)["records"][0]["parent_locator_coverage_at5"] is None


def test_source_body_binding_is_checked_before_building_units():
    corpus = [{"revision_id": "r", "source_id": "synthetic", "locator": "第十条", "quote": "模拟原文", "category": "identity"}]
    binding = {"revision_id": "r", "source_id": "synthetic", "body_sha256": "wrong"}
    with pytest.raises(ValueError, match="正文指纹"):
        build_units(corpus, [binding])


def test_missing_reference_or_oversized_question_cannot_be_scored():
    case = {"id": "x", "question": "模拟", "expected_behavior": "conditional_answer",
            "archived_evidence": [{"revision": "missing", "locator": "unknown"}]}
    with pytest.raises(ValueError, match="参考定位缺失"):
        evaluate_units([case], [unit("a", ["one"])])
    with pytest.raises(ValueError, match="300"):
        rank_units("甲"*301, [])


def test_existing_fee_topic_can_bridge_words_without_exact_bigram_overlap():
    fee = unit("fee", ["fee-page"], "工本费及减免条件。")
    generic = unit("generic", ["generic-page"], "本地户籍身份证到期换领。")
    q = "本地户籍身份证到期换领费用多少？"
    assert rank_units(q, [fee, generic])[0] == generic
    assert rank_units(q, [fee, generic], "topic_priority_v1")[0] == fee


def test_topic_profile_does_not_receive_gold_answers_or_reference_labels():
    corpus = [unit("fee", ["fee-page"], "工本费按规定执行。"), unit("other", ["other"], "到期换领。")]
    a = {"id": "a", "question": "到期换领费用？", "expected_behavior": "conditional_answer",
         "archived_evidence": [{"revision": "r", "locator": "fee-page"}], "ai_reference_answer": "GOLD A"}
    b = dict(a, ai_reference_answer="GOLD B", expected_behavior="stop", archived_evidence=[])
    left = evaluate_units([a], corpus, "topic_priority_v1")["records"][0]
    right = evaluate_units([b], corpus, "topic_priority_v1")["records"][0]
    assert left["selected_units"] == right["selected_units"]
    with pytest.raises(ValueError, match="未知"):
        rank_units("到期换领费用", corpus, "unknown")


def test_collection_intent_does_not_prioritize_application_receipt_word_alone():
    application = unit("application", ["application"], "申请领取居民身份证，到窗口申请领取新证。")
    collection = unit("collection", ["collection"], "公民本人领取居民身份证，应核对信息并在《居民身份证申领登记表》签名；委托亲属代为领取的应出示凭证。")
    selected = rank_units("新证必须本人到窗口领取吗？", [application, collection], "specific_topic_phase_v1")
    assert selected[0] == collection


def test_generic_eligibility_words_alone_do_not_change_literal_ranking():
    corpus = [unit("a", ["a"], "到期的申请事项。"), unit("b", ["b"], "材料及户籍字段。")]
    q = "户籍到期要了解材料"
    assert rank_units(q, corpus, "specific_topic_phase_v1") == rank_units(q, corpus, "literal")

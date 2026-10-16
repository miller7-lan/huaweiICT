"""Synthetic engineering questions only; no actual policy acceptance labels."""

import copy

import pytest

from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_query import question_signals_v2, source_topics_v3, signal_text, intent_source_key_v3
from verify_knowledge_source_units import evaluate_units, parent_keys, rank_units


def unit(name, text, title="模拟身份证资料", parents=None):
    return {"id": name, "revision_id": "synthetic-"+name, "source_id": "synthetic-only",
        "source_title": title, "heading_surface": None, "text_for_ranking": text,
        "text_sha256": fingerprint(text.encode()), "flags": [],
        "spans": [{"locator": p, "parent_ordinal": i, "range": [0, len(text)]} for i, p in enumerate(parents or [name])]}


def test_ocr_spaces_are_only_removed_in_derived_topic_view():
    original = "在 公 安 派 出 所 核 对 指 纹 信 息。"
    assert {"location", "capture", "verification"} <= source_topics_v3(original)
    assert signal_text(original) == "在公安派出所核对指纹信息。"
    entry = unit("ocr", original)
    before = copy.deepcopy(entry)
    rank_units("身份证在哪里办？", [entry], "intent_normalized_v3")
    assert entry == before


def test_physical_card_return_action_is_distinct_from_old_card_validity():
    action = unit("return", "领取新证时，应交回原证。")
    validity = unit("validity", "旧身份证在有效期内继续有效。")
    assert "document_handling" in source_topics_v3(action["text_for_ranking"])
    assert "document_handling" not in source_topics_v3(validity["text_for_ranking"])
    assert rank_units("换证时旧身份证会收走还是只供核验？", [validity, action], "intent_normalized_v3")[0] == action


def test_adult_local_query_does_not_prioritize_minor_or_nonlocal_list():
    normal = unit("normal", "换领时交验原居民身份证。")
    minor = unit("minor", "未满16周岁的申请人到期换领，需要户口簿、原件。")
    other = unit("nonlocal", "跨户籍所在地市异地办证，到期换领需提交证明材料。")
    question = "成年本地户籍本人到期换领要带哪些材料？"
    assert rank_units(question, [minor, other, normal], "intent_normalized_v3")[0] == normal
    assert intent_source_key_v3("非本地户籍换证需要哪些材料？", other, True)[3] == 0


def test_duration_question_with_collection_words_keeps_time_focus():
    time = unit("time", "身份证从受理到领证需要二十个自然日。")
    collection = unit("collection", "本人领取居民身份证，应核对信息并签名。")
    question = "身份证申请之后几天才能领证？"
    assert question_signals_v2(question)["primary"] == "duration"
    assert rank_units(question, [collection, time], "intent_normalized_v3")[0] == time


def test_explicit_calendar_unit_precedes_unrelated_temporary_time():
    ordinary = unit("ordinary", "居民身份证从受理到领证为25个自然日。")
    temporary = unit("temporary", "临时居民身份证应三日内制作发放。")
    generic = unit("generic", "进一步缩短审批时限。")
    selected = rank_units("身份证办理多久，按自然日算吗？", [generic, temporary, ordinary], "intent_normalized_v3")
    assert selected[0] == ordinary


def test_information_side_of_card_is_not_a_privacy_rule():
    assert "privacy" not in source_topics_v3("剪角位置为证件个人信息面左下角。")
    assert "privacy" in source_topics_v3("不得擅自复印、扫描居民身份证。")


def test_separate_verification_and_consultation_demands_both_receive_evidence():
    verify = unit("verify", "申请换领时核对人像、指纹信息。")
    restrict = unit("restrict", "不得擅自记录身份证记载的个人信息。")
    background = [unit("background"+str(i), "公民个人信息应当保密，身份证号码是身份信息。") for i in range(7)]
    question = "换证时核验哪些信息；咨询时要把身份证号码发给你吗？"
    chosen = rank_units(question, [*background, restrict, verify], "intent_normalized_v3")
    assert verify in chosen and restrict in chosen
    assert chosen == rank_units(question, list(reversed([*background, restrict, verify])), "intent_normalized_v3")


def test_new_profile_preserves_cross_page_budget_and_original_spans():
    cross = unit("cross", "居民身份证办理周期为30个自然日。", parents=["p1", "p2"])
    too_large = unit("oversized", "居民身份证办理周期为自然日。", parents=["z"+str(i) for i in range(6)])
    corpus = [cross, too_large, *[unit(str(i), "身份证办理为20个自然日。") for i in range(7)]]
    before = copy.deepcopy(corpus)
    selected = rank_units("居民身份证多久领到，怎么算自然日？", corpus, "intent_normalized_v3")
    assert len(selected) <= 5 and len(set().union(*(parent_keys(u) for u in selected))) <= 5
    assert too_large not in selected and corpus == before


def test_new_selector_does_not_receive_ai_answer_or_target_labels():
    corpus = [unit("fee", "身份证工本费按收费标准执行。"), unit("other", "领取新证时签名。")]
    case = {"id": "a", "question": "身份证工本费多少钱？", "expected_behavior": "conditional_answer",
        "archived_evidence": [], "ai_reference_answer": "GOLD A"}
    other = dict(case, expected_behavior="stop", ai_reference_answer="GOLD B")
    left = evaluate_units([case], corpus, "intent_normalized_v3")["records"][0]
    right = evaluate_units([other], corpus, "intent_normalized_v3")["records"][0]
    assert left["selected_units"] == right["selected_units"]
    with pytest.raises(ValueError, match="300"):
        rank_units("字"*301, corpus, "intent_normalized_v3")

"""Generic loss-declaration and event-year regressions; no network or model calls."""
import pytest

from gov_mcp import claim_binding, scope_gate_v2
from gov_mcp import frozen_qa_flow_v3 as flow


@pytest.mark.parametrize("question", [
    "证件遗失是不是必须先登报作废声明？",
    "归档是否要求补领前发布作废公告？",
    "只按归档解释作废申报的材料。",
])
def test_loss_declaration_is_not_itself_a_policy_validity_question(question):
    assert scope_gate_v2.scope_gate(question) is None


@pytest.mark.parametrize("question", [
    "该规定是否已经作废？",
    "这个作废声明模板目前还有效吗？",
    "关于作废公告的规则是否已经废止？",
])
def test_actual_policy_validity_questions_still_stop(question):
    assert scope_gate_v2.scope_gate(question) == "CURRENT_VALIDITY_NOT_ESTABLISHED"


TITLE = {"source_id": "DOC", "evidence_kind": "source_title", "text": "2025年业务服务通知"}


@pytest.mark.parametrize("actor", ["省级行政部门", "某市公安机关"])
def test_a_quoted_event_year_is_distinct_from_the_document_year(actor):
    claim = f"2020年{actor}通过便民措施将办理时限从50日缩减至20日。"
    quote = f"2020年，{actor}通过便民措施将办理时限从50日缩减至20日。"
    out = {"assertions": [{"source_id": "DOC", "claim": claim, "quote": quote}]}
    assert claim_binding.document_year_failures(out, [TITLE]) == []


@pytest.mark.parametrize("claim,quote", [
    ("2020年通知规定时限20日。", "2020年，省级行政部门将办理时限缩减至20日。"),
    ("2020年省级行政部门通过便民措施将办理时限缩减至20日。", "2021年，省级行政部门通过便民措施将办理时限缩减至20日。"),
    ("2020年省级行政部门通过便民措施将办理时限缩减至20日。", "2020年，某市公安机关通过便民措施将办理时限缩减至20日。"),
])
def test_event_exception_does_not_allow_wrong_document_year_or_unquoted_actor(claim, quote):
    out = {"assertions": [{"source_id": "DOC", "claim": claim, "quote": quote}]}
    assert claim_binding.document_year_failures(out, [TITLE])


def test_unverified_summary_inference_becomes_a_gap_not_a_displayed_conclusion():
    raw = {"outcome": "answer", "answer": "不能据此推断所有服务收费均免除。", "assertions": [], "unknowns": []}
    final = {"outcome": "answer", "answer": "已核验的原文事实。", "assertions": [], "unknowns": []}
    result = flow.note_inference_gap(raw, final)
    assert result["answer"] == final["answer"] and result["assertions"] == []
    assert flow.INFERENCE_GAP in result["unknowns"]
    assert flow.label_kind(result, "answer") == "answer_with_gaps"
    assert final["unknowns"] == []


def test_inference_gap_marker_never_promotes_a_stop_or_duplicates_a_retained_inference():
    raw = {"outcome": "answer", "answer": "不能据此作出结论。", "assertions": [], "unknowns": []}
    stopped = {"outcome": "stop", "answer": "停止", "assertions": [], "unknowns": []}
    assert flow.note_inference_gap(raw, stopped) is stopped
    kept = {**raw, "unknowns": []}
    assert flow.note_inference_gap(raw, kept) is kept

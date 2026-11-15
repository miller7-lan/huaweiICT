"""Synthetic structural checks, not model/business acceptance evidence."""
import copy

import pytest

from gov_mcp import frozen_qa
from gov_mcp.frozen_retrieval import EvidenceIndex, MAX_LINKS, VERSION, compound_queries
from gov_mcp.frozen_qa_server import FrozenQAService, code_fingerprints


def unit(source, locator, text, kind="html_paragraph", **extra):
    return {"source_id": source, "locator": locator, "text": text,
            "evidence_kind": kind, "text_sha256": frozen_qa.sha(text), **extra}


def test_distinct_subquestions_retain_low_frequency_fee_evidence():
    units = [unit("PHOTO", f"第{i}条", "证件照片不满意可以重新拍照三次。", "html_article") for i in range(12)]
    units.append(unit("FEE", "正文段落1", "不得收取照相费。"))
    query = "证件照片不满意可以重拍几次？拍照是否能收照相费？"
    assert len(list(compound_queries(query))) == 2
    result = EvidenceIndex(units).retrieve(query)
    assert ("FEE", "正文段落1") in {(u["source_id"], u["locator"]) for u in result["evidence"]}
    assert result["plan"]["semantic_coverage_verified"] is False


def test_context_preserves_exact_originals_and_no_input_mutation():
    units = [unit("RULE", "正文段落1", "特殊办理适用以下条件。"),
             unit("RULE", "正文段落2", "仅限测试范围；例外情况仍需人工核对。"),
             unit("OTHER", "正文段落2", "另一来源不相连。")]
    original = copy.deepcopy(units)
    idx = EvidenceIndex(units)
    result = idx.retrieve("特殊办理适用条件", k=1)
    assert units == original and result == idx.retrieve("特殊办理适用条件", k=1)
    keyed = {(u["source_id"], u["locator"]): u for u in units}
    for hit in result["evidence"]:
        expected = keyed[hit["source_id"], hit["locator"]]
        assert all(hit[key] == value for key, value in expected.items())
    reasons = {(u["source_id"], u["locator"]): u["reasons"] for u in result["plan"]["reasons"]}
    assert "adjacent-paragraph" in reasons["RULE", "正文段落2"]
    assert ("OTHER", "正文段落2") not in reasons


def test_context_does_not_bridge_revisions_or_article_numbers():
    units = [unit("S", "正文段落1", "换领办理所需材料", revision_id="v1"),
             unit("S", "正文段落2", "仅限另一个版本", revision_id="v2"),
             unit("S", "第二条", "相邻法条不能按数组顺序拼接", "html_article")]
    packet = EvidenceIndex(units).retrieve("换领所需材料", k=1)
    assert not any("adjacent-paragraph" in r["reasons"] for r in packet["plan"]["reasons"])


def test_table_header_discovery_keeps_correct_sheet_and_cells():
    units = [unit("T", "Excel工作表证件!第2行", "项目名称 | 设立依据 | 收费标准", "xlsx_row", cells=["B2", "C2", "D2"]),
             unit("T", "Excel工作表考试!第2行", "项目名称 | 设立依据 | 收费标准", "xlsx_row"),
             unit("T", "Excel工作表证件!第12行", "居民身份证 | 文号 | 20元", "xlsx_row", cells=["B12", "D12"])]
    result = EvidenceIndex(units).retrieve("居民身份证目录的收费标准是什么？")
    assert [u["locator"] for u in result["evidence"]] == [units[2]["locator"], units[0]["locator"]]
    assert result["evidence"][1]["cells"] == ["B2", "C2", "D2"]


def test_bounded_plan_and_duplicate_locator_rejection():
    units = [unit("S", f"正文段落{i}", "证件办理材料和时限分别说明") for i in range(100)]
    packet = EvidenceIndex(units).retrieve("证件办理材料是什么？办理时限多久？", k=2)
    assert packet["plan"]["primary_count"] <= 4
    assert packet["plan"]["linked_count"] <= MAX_LINKS
    assert len(packet["evidence"]) == len({(u["source_id"], u["locator"]) for u in packet["evidence"]})
    with pytest.raises(ValueError, match="AMBIGUOUS_SOURCE_LOCATOR"):
        EvidenceIndex([units[0], units[0]])
    for k in (0, -1, 21, True):
        with pytest.raises(ValueError, match="RETRIEVAL_K_OUT_OF_RANGE"):
            EvidenceIndex(units).retrieve("证件", k)


def test_no_model_call_or_retrieval_for_private_question(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("private question must stop before retrieval or model")
    monkeypatch.setattr(frozen_qa, "retrieve_with_plan", forbidden)
    result = frozen_qa.ask("测试00000000000000000X", [], "proposed", forbidden)
    assert result["gate"] == "PRIVATE_FACT_NOT_ACCEPTED"
    assert result["retrieval"]["evidence"] == []
    service = FrozenQAService([], "c" * 64)
    monkeypatch.setattr(service.index, "retrieve", forbidden)
    assert service.retrieve("测试00000000000000000X")["retrieval_plan"] is None


def test_mcp_and_python_share_packet_and_source_fingerprint():
    units = [unit("A", "正文段落1", "换领交验居民身份证。")]
    service = FrozenQAService(units, "c" * 64)
    result = service.retrieve("换领交验什么")
    packet = frozen_qa.retrieve_with_plan(units, "换领交验什么")
    assert result["retrieval_plan"]["queries"] == frozen_qa.query_hints(packet["plan"], packet["evidence"])
    assert result["retrieval_plan"]["version"] == VERSION
    assert "frozen_retrieval.py" in code_fingerprints()
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert result["current_applicability"] == "unknown"


@pytest.mark.parametrize("question", [None, "", " " * 2, "a" * 501])
def test_verify_rejects_invalid_questions_before_contract_or_retrieval(question):
    assert FrozenQAService([], "c" * 64).verify(question, {})["error"] == "QUESTION_REQUIRED_MAX_500_CHARS"


def citation(claim, source, quote=None):
    return {"claim": claim, "source_id": source["source_id"], "locator": source["locator"],
            "quote": source["text"] if quote is None else quote}


def candidate(text, assertions):
    return {"outcome": "answer", "answer": text, "assertions": assertions, "stop_reason": "", "unknowns": []}


def test_document_years_timestamp_and_printed_date_are_typed_not_policy_quantities():
    a = unit("A", "正文段落1", "普通办理期限为25个自然日。")
    title = unit("A", "标题", "河南公安机关2030年便民措施", "source_title")
    date = unit("A", "发布日期", "2030-04-12 09:18:04.0", "page_publication_date", calendar_date="2030-04-12")
    b = unit("B", "正文段落1", "普通办理期限为15个工作日。")
    heading = unit("B", "文头", "2028年2月14日 财综[2028]34号")
    units = [a, title, date, b, heading]
    good = candidate("2030年普通办理为25个自然日；2028年普通办理为15个工作日。", [
        citation("2030年措施的普通办理为25个自然日。", a),
        citation("网页标题为河南公安机关2030年便民措施。", title),
        citation("网页发布日期为2030-04-12 09:18:04.0。", date),
        citation("2028年通知的普通办理为15个工作日。", b),
        citation("该2028年通知标注日期为2028年2月14日，文号为财综[2028]34号。", heading)])
    assert frozen_qa.verify(good, units)["passed"]
    for false_text in ("网页发布日期为2030-04-12 09:18:05.0。", "施行日期为2030-04-12。",
                       "办理期限为2030年。", "有效期为2030年文件。", "2031年文件规定办理期限25个自然日。"):
        bad = {**good, "answer": false_text}
        assert not frozen_qa.verify(bad, units)["passed"], false_text
    missing_year_citations = {**good, "assertions": [good["assertions"][0], good["assertions"][3]]}
    assert not frozen_qa.verify(missing_year_citations, units)["passed"]


def test_cell_suffix_normalization_is_exact_and_returned_canonically():
    row = unit("TABLE", "Excel工作表证件!第36行", "居民身份证 | 20元", "xlsx_row", cells=["D36", "G36"])
    item = citation("费用为20元", row)
    item["locator"] += " | cells=D36,G36"
    raw = candidate("费用为20元", [item])
    unchanged = copy.deepcopy(raw)
    service = FrozenQAService([row], "c" * 64)
    result = service.verify("居民身份证费用", raw)
    assert result["checks"]["passed"] and result["final"]["assertions"][0]["locator"] == row["locator"]
    assert raw == unchanged
    for bad_locator in (row["locator"] + " | cells=D37,G37", row["locator"] + " | cells=D36",
                        "Excel工作表其他!第36行 | cells=D36,G36"):
        bad = candidate("费用为20元", [{**item, "locator": bad_locator}])
        assert service.verify("居民身份证费用", bad)["final"]["outcome"] == "stop"


def test_year_in_source_id_or_effective_date_does_not_certify_document_year():
    row = unit("SOURCE-2030", "正文段落1", "自2030年1月1日起免征测试费用。")
    output = candidate("2030年首次申领停征条款规定免征。", [citation("2030年通知规定免征。", row)])
    assert not frozen_qa.verify(output, [row])["passed"]


def test_postal_collection_survives_single_question_and_generic_collection_matches():
    units = [unit("PICKUP", f"第{i}条", "异地委托领证时，代领人应当出示居民身份证领取凭证。")
             for i in range(12)]
    units.append(unit("POST", "正文段落2", "申领居民身份证后，可选择自费邮寄服务，将证件直接邮寄至指定地址。"))
    for question in ("居民身份证寄送的邮费由谁承担？", "委托领证要出示什么？不用到派出所领取时由谁承担邮费？"):
        packet = EvidenceIndex(units).retrieve(question)
        assert any(u["source_id"] == "POST" for u in packet["evidence"][:packet["plan"]["primary_count"]])


def test_first_issue_fee_requires_document_year_and_waiver_start_date():
    old = unit("MOF-FEE-2007", "正文段落4", "首次申领居民身份证收取工本费每证20元。")
    heading = unit("MOF-FEE-2007", "正文段落1", "2007年2月14日 财综[2007]34号")
    waiver = unit("MOF-FIRST-ISSUE-2018", "正文段落4", "自2018年4月1日起，停征首次申领居民身份证工本费。")
    service = FrozenQAService([old, heading, waiver], "a" * 64)
    question = "按归档对比首次申领居民身份证工本费和停征日期"
    old_claim = "2007年财政通知记载首次申领居民身份证工本费每证20元。"
    good = candidate(old_claim + waiver["text"], [
        citation(old_claim, old), citation("旧通知文头", heading), citation(waiver["text"], waiver)])
    assert service.verify(question, good)["final"]["outcome"] == "answer"
    for wording in ("文头日期为", "文头所载"):
        printed_date = f"旧通知的{wording}2007年2月14日。"
        with_date = candidate(old_claim + waiver["text"], [
            citation(old_claim, old), citation(printed_date, heading), citation(waiver["text"], waiver)])
        assert service.verify(question, with_date)["final"]["outcome"] == "answer"
        with_date["assertions"][1]["claim"] = printed_date.replace("14日", "15日")
        assert service.verify(question, with_date)["final"]["outcome"] == "stop"
    unqualified = candidate(old["text"] + waiver["text"], [
        citation(old["text"], old), citation(waiver["text"], waiver)])
    result = service.verify(question, unqualified)
    assert result["final"]["outcome"] == "stop"
    assert "FIRST_ISSUE_OLD_FEE_DOCUMENT_YEAR_REQUIRED" in result["checks"]["failures"]
    missing_date = "停征首次申领居民身份证工本费。"
    undated = candidate(old_claim + missing_date, [
        citation(old_claim, old), citation("旧通知文头", heading), citation(missing_date, waiver)])
    assert "FIRST_ISSUE_WAIVER_EFFECTIVE_DATE_REQUIRED" in service.verify(question, undated)["checks"]["failures"]
    assert service.verify(question, candidate(waiver["text"], [citation(waiver["text"], waiver)]))["final"]["outcome"] == "answer"

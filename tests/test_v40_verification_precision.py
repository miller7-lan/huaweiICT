"""V4.0: dates and document numbers that a cited quote prints are not read as unquoted facts,
while a different date, role, issuer or number still fails."""
from gov_mcp.claim_binding import document_number_failures, document_number_quoted
from gov_mcp.frozen_qa import verify

PUB = {"source_id": "S-A", "locator": "网页发布日期", "text": "2026-05-05 09:18:04.0",
       "evidence_kind": "page_publication_date", "calendar_date": "2026-05-05"}
MOD = {"source_id": "S-A", "locator": "网页最后修改日期", "text": "2026-05-11 09:23",
       "evidence_kind": "page_modified_date", "calendar_date": "2026-05-11"}
PUB_B = {"source_id": "S-B", "locator": "网页发布日期", "text": "2022-08-16 09:22:33.0",
         "evidence_kind": "page_publication_date", "calendar_date": "2022-08-16"}
HEAD_A = {"source_id": "F-A", "locator": "正文段落1", "text": "2018年4月12日 财税〔2018〕37号", "evidence_kind": "text"}
HEAD_B = {"source_id": "F-B", "locator": "正文段落1", "text": "2007年2月14日 财综[2007]34号", "evidence_kind": "text"}


def claim(unit, text, quote=None):
    return {"claim": text, "source_id": unit["source_id"], "locator": unit["locator"], "quote": quote or unit["text"]}


def answer(text, *assertions):
    return {"outcome": "answer", "answer": text, "assertions": list(assertions), "unknowns": [], "stop_reason": ""}


def failures(output, evidence):
    return verify(output, evidence)["failures"]


def test_untyped_timestamp_printed_by_a_cited_quote_passes():
    out = answer("两页最后修改日分别为：A页2026-05-11 09:23。", claim(MOD, "A页最后修改日期为2026-05-11 09:23。"))
    assert failures(out, [MOD]) == []


def test_untyped_timestamp_not_printed_still_fails():
    out = answer("两页最后修改日分别为：A页2026-05-12 09:23。", claim(MOD, "A页最后修改日期为2026-05-11 09:23。"))
    assert any(f.startswith("NUMBER_NOT_QUOTED") for f in failures(out, [MOD]))


def test_date_keeps_its_role():
    out = answer("A页发布时间为2026-05-11 09:23。", claim(MOD, "A页发布时间为2026-05-11 09:23。"))
    assert any(f.startswith("DATE_NOT_QUOTED:page_publication_date") for f in failures(out, [MOD]))


def test_comparison_claim_repeated_for_each_source_passes():
    text = "B页的发布时间为2022-08-16 09:22:33.0，早于A页的发布时间2026-05-05 09:18:04.0。"
    out = answer(text, claim(PUB_B, text), claim(PUB, text))
    assert failures(out, [PUB, PUB_B]) == []


def test_claim_cannot_take_a_date_only_another_source_prints():
    out = answer("A通知文头日期为2018年4月12日。",
                 claim(HEAD_A, "A通知文头日期为2018年4月12日。"),
                 claim(HEAD_B, "B通知文头日期为2007年2月14日，A通知文头日期2018年4月12日晚于该日期。"))
    assert "DATE_NOT_QUOTED:document_date:2018-04-12" in failures(out, [HEAD_A, HEAD_B])
    untyped = answer("见两份通知。", claim(HEAD_B, "B通知（2018-04-12）。"))
    assert any(f.startswith("NUMBER_NOT_QUOTED") for f in failures(untyped, [HEAD_A, HEAD_B]))


def test_document_number_with_words_before_the_issuer_passes():
    assert document_number_quoted("规范收费的财综〔2007〕34号", {"财综〔2007〕34号"})
    assert document_number_quoted("未逐一列出财综〔2007〕34号", {"财综〔2007〕34号"})


def test_other_issuer_year_or_number_still_fails():
    assert not document_number_quoted("粤财综〔2007〕34号", {"财综〔2007〕34号"})
    assert not document_number_quoted("规范收费的财综〔2007〕35号", {"财综〔2007〕34号"})
    assert not document_number_quoted("规范收费的财综〔2008〕34号", {"财综〔2007〕34号"})


def test_document_number_failures_use_the_fix():
    out = answer("见规范收费的财综〔2007〕34号。", claim(HEAD_B, "规范居民身份证收费的财综〔2007〕34号文头日期为2007年2月14日。"))
    units = {(HEAD_B["source_id"], HEAD_B["locator"]): HEAD_B}
    assert document_number_failures(out, units) == []


def test_status_disclaimer_or_gap_wording_is_not_a_lost_inference():
    from gov_mcp.frozen_qa_flow_v3 import INFERENCE_GAP, note_inference_gap
    final = answer("- 可在全省任一窗口办理。", claim(MOD, "可在全省任一窗口办理。"))
    for raw_answer in ("可在全省任一窗口办理。以上为归档资料记载，不代表现行政策。",
                       "可在全省任一窗口办理。邮寄查询渠道现有证据未提供，不能据此作答；"):
        raw = answer(raw_answer, claim(MOD, "可在全省任一窗口办理。"))
        assert INFERENCE_GAP not in note_inference_gap(raw, final)["unknowns"]
    raw = answer("可在全省任一窗口办理，但不能由受理地推出签发机关。", claim(MOD, "可在全省任一窗口办理。"))
    assert INFERENCE_GAP in note_inference_gap(raw, final)["unknowns"]

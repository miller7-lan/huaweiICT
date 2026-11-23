"""Regression cases for frozen-question verification and the offline acceptance tools."""
import json
import sys
from pathlib import Path

import pytest

import export_flow_v3_diagnostics as diagnostics
import extract_fact_cards as cards
import frozen_flow_native as native
import run_frozen_qa_native as runner
from gov_mcp import frozen_qa as qa, frozen_qa_flow_v3 as flow
from gov_mcp.frozen_qa_flow_server import FlowService
from gov_mcp.frozen_retrieval import FIRST_ISSUE_FEE, FIRST_ISSUE_WAIVER


def unit(source, text, locator="正文段落1", kind="html_paragraph", **metadata):
    return {"source_id": source, "code": source, "locator": locator, "text": text,
            "evidence_kind": kind, "text_sha256": qa.sha(text), "rank": 1, **metadata}


def item(text, evidence, quote=None):
    return {"claim": text, "source_id": evidence["source_id"], "locator": evidence["locator"],
            "quote": evidence["text"] if quote is None else quote}


def answer(*assertions, unknowns=()):
    return {"outcome": "answer", "answer": "\n".join(a["claim"] for a in assertions),
            "assertions": list(assertions), "stop_reason": "", "unknowns": list(unknowns)}


def test_quantities_are_bound_to_their_business_branch_and_their_own_quote():
    shared = unit("A", "首次申领工本费20元；损坏换领工本费40元。")
    assert flow.check(answer(item("首次申领20元，损坏换领40元", shared)), [shared])["passed"]
    for suspect in (item("首次申领40元，损坏换领20元", shared),
                    item("首次申领40元", shared, "工本费40元")):
        assert "A1_QUANTITY_BRANCH_MISMATCH" in flow.check(answer(suspect), [shared])["failures"]
    target, other = unit("A", "本事项交材料3份。"), unit("B", "其他事项交材料9份。")
    draft = answer(item("本事项交材料9份", target), item(other["text"], other))
    assert "A1_QUANTITY_NOT_QUOTED:9份" in flow.check(draft, [target, other])["failures"]
    assert flow.check(answer(item("本事项交材料3份", target), item(other["text"], other)), [target, other])["passed"]


def test_omitting_a_restriction_is_rejected_even_if_the_quote_is_short():
    evidence = unit("A", "仅对材料齐全的申请人，可以当天发证。")
    for claim in ("所有申请人可以当天发证", "仅对所有申请人，可以当天发证"):
        draft = answer(item(claim, evidence, "可以当天发证"))
        assert "A1_CLAIM_OMITS_SOURCE_LIMITATION" in flow.check(draft, [evidence])["failures"]
    assert flow.check(answer(item("仅对材料齐全的申请人，可以当天发证", evidence)), [evidence])["passed"]


@pytest.mark.parametrize(("scope", "classifier"), [
    ("重新拍照", "次"), ("有效期限为", "个月"), ("办理时限为", "个工作日"),
])
def test_chinese_and_arabic_quantities_compare_by_value(scope, classifier):
    evidence = unit("A", scope + "三" + classifier + "。")
    for numeral, valid in (("3", True), ("九", False), ("9", False)):
        draft = answer(item(scope + numeral + classifier, evidence))
        assert flow.check(draft, [evidence])["passed"] is valid


def test_document_number_preserves_issuing_authority_and_bracket_equivalence():
    evidence = unit("A", "本事项依据财综[2007]34号。")
    assert flow.check(answer(item("依据财综〔2007〕34号", evidence)), [evidence])["passed"]
    bad = flow.check(answer(item("依据豫财综〔2007〕34号", evidence)), [evidence])
    assert "A1_DOCUMENT_NUMBER_NOT_QUOTED" in bad["failures"]


def test_complete_dates_and_document_versions_have_separate_sources():
    body = unit("A", "新版材料须留存。", revision_id="new")
    old = unit("A", "2018年测试通知", "旧版标题", "source_title", revision_id="old")
    page = unit("A", "网页发布日期：2026-01-12", "发布日期", "page_publication_date",
                calendar_date="2026-01-12", revision_id="new")
    for year in ("2018", "2026"):
        draft = answer(item(year + "年通知要求新版材料须留存", body), item(old["text"], old), item(page["text"], page))
        assert not flow.check(draft, [body, old, page])["passed"]
    assert flow.check(answer(item("网页发布日期为2026-01-12", page)), [page])["passed"]
    a, b = unit("B", "档案编号2024，分第4组。"), unit("C", "录入有4类文件、共3页。")
    fabricated = answer(item("2024年4月3日为办理起点", a), item("录入有4类文件、共3页", b))
    assert "DATE_NOT_QUOTED:calendar_date:2024-04-03" in flow.check(fabricated, [a, b])["failures"]
    dated = unit("C", "2024年4月3日为办理起点。")
    assert flow.check(answer(item("2024-04-03为办理起点", dated)), [dated])["passed"]


@pytest.mark.parametrize(("question", "gate"), [
    ("我的身份证号是000000 00000000 0000，可以办理吗？", "PRIVATE_FACT_NOT_ACCEPTED"),
    ("杭州居民换领身份证多少钱？", "OUTSIDE_HENAN_SCOPE"),
    ("这项规定还有效吗？", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("安徽合肥居民怎样办理？", "OUTSIDE_HENAN_SCOPE"),
    ("不涉及江苏，但办理杭州的业务", "OUTSIDE_HENAN_SCOPE"),
    ("只读归档，不涉及江苏，只问河南身份证换领的归档收费。", None),
    ("2018年当时是否有效？", None),
    ("请提交河南居民身份证换领申请，并帮我在线缴费。", "ACTION_NOT_SUPPORTED"),
    ("只看归档，申请材料怎么提交？", None),
])
def test_archived_question_scope(question, gate):
    assert qa.scope_gate(question) == gate


def test_excel_cell_must_be_in_the_cited_row():
    row = unit("A", "居民身份证 | 20元", "Excel工作表收费!第36行", "xlsx_row", cells=["A36", "G36"])
    assert flow.check(answer(item("G36记载工本费20元。", row)), [row])["passed"]
    assert "CELL_NOT_CITED:G37" in flow.check(answer(item("G37记载工本费20元。", row)), [row])["failures"]
    assert flow.check(answer(item("工作表收费!G36记载工本费20元。", row)), [row])["passed"]
    assert "CELL_NOT_CITED:其他!G36" in flow.check(answer(item("工作表其他!G36记载工本费20元。", row)), [row])["failures"]


def test_distinct_table_columns_cannot_borrow_one_anothers_numbers():
    heading = unit("A", "项目名称 | 设立依据 | 收费标准 | 办理时限",
                   "Excel工作表收费!第1行", "xlsx_row", cells=["A1", "B1", "C1", "D1"])
    row = unit("A", "身份证 | 条例 | 20元 | 3天",
               "Excel工作表收费!第2行", "xlsx_row", cells=["A2", "B2", "C2", "D2"])
    assert flow.check(answer(item("收费标准20元，办理时限3天", row)), [heading, row])["passed"]
    assert "A1_QUANTITY_COLUMN_MISMATCH" in flow.check(
        answer(item("收费标准3天，办理时限20元", row)), [heading, row])["failures"]


def test_table_document_numbers_drop_prose_and_remain_in_the_named_column():
    heading = unit("A", "项目名称 | 设立依据 | 收费标准",
                   "Excel工作表收费!第1行", "xlsx_row", cells=["A1", "B1", "C1"])
    row = unit("A", "身份证 | 豫财办行〔2006〕39号 | 财综〔2007〕34号，财税〔2018〕37号",
               "Excel工作表收费!第2行", "xlsx_row", cells=["A2", "B2", "C2"])
    claim = "居民身份证这一行的收费标准列列出的是财综〔2007〕34号、财税〔2018〕37号两个文号。"
    assert flow.check(answer(item(claim, row)), [heading, row])["passed"]
    listed = "收费标准列引用财综〔2007〕34号、财税〔2018〕37号两份文件。"
    assert flow.check(answer(item(listed, row)), [heading, row])["passed"]
    for phrasing in ("收费标准列引用了《财综〔2007〕34号》和《财税〔2018〕37号》两份文件。",
                     "收费标准列（C2）引用了财综〔2007〕34号和财税〔2018〕37号两份文件。"):
        assert flow.check(answer(item(phrasing, row)), [heading, row])["passed"]
    assert "A1_QUANTITY_NOT_QUOTED:3份" in flow.check(
        answer(item(listed.replace("两份", "三份"), row)), [heading, row])["failures"]
    wrong = answer(item("收费标准列列出的是豫财办行〔2006〕39号", row))
    assert "A1_DOCUMENT_NUMBER_COLUMN_MISMATCH" in flow.check(wrong, [heading, row])["failures"]
    assert flow.check(answer(item("收费标准列（C2）列出的是财综〔2007〕34号。", row)), [heading, row])["passed"]
    for wrong_address in ("收费标准列（B2）列出的是财综〔2007〕34号。", "B2为收费标准。"):
        assert "A1_TABLE_COLUMN_CELL_MISMATCH" in flow.check(answer(item(wrong_address, row)),
                                                           [heading, row])["failures"]


def test_empty_drafts_and_unsupported_metadata_cannot_be_released():
    body, title = unit("A", "费用20元。"), unit("B", "归档服务事项标题", "网页标题", "source_title")
    assert flow.settle_draft(answer(item("", body)), [body], [body])["result_kind"] == "stop"
    assert flow.settle_draft(answer(item("可以通过审核", title)), [title], [title])["result_kind"] == "stop"
    assert flow.check(answer(item("网页标题为归档服务事项标题", title)), [title])["passed"]


def test_document_heading_can_support_an_explicit_filing_date():
    heading = unit("A", "2007年2月14日 财综[2007]34号")
    for claim in ("2007年通知的年份由文头日期标明为2007年2月14日。", "文头日期为2007-02-14。"):
        assert flow.check(answer(item(claim, heading)), [heading])["passed"]
    for claim in ("施行日期为2007年2月14日。", "文头日期为2007年2月15日。",
                  "2008年通知的年份由文头日期标明为2007年2月14日。",
                  "2007年通知的年份由文头日期标明为2007年2月14日，工本费20元。"):
        assert not flow.check(answer(item(claim, heading)), [heading])["passed"]


def test_scan_item_and_catalogue_year_keep_their_own_evidence():
    title = unit("P", "河南省公安厅关于印发2021年便民利企服务新举措的通知（豫公〔2021〕16号）",
                 "资料目录登记标题（目录元数据，未经人工对照原件）", "source_title")
    scan = unit("P", "二、换领、补领居民身份证实行省内通办。群众无需到户籍地派出所，可就近办理。",
                "PDF第5页右半页（印刷页码5），第二项", "attributed_ai_transcription")
    claim = "归档的2021年便民利企服务新举措第二项载明：换领、补领居民身份证实行省内通办。"
    identification = ("该2021年便民利企服务新举措的年份出处为资料目录登记的"
                      "《河南省公安厅关于印发2021年便民利企服务新举措的通知》（豫公〔2021〕16号）。")
    assert flow.check(answer(item(claim, scan), item(identification, title)), [scan, title])["passed"]
    wrong = flow.check(answer(item(claim.replace("第二项", "第3项"), scan), item(identification, title)), [scan, title])
    assert "SCAN_SECTION_NOT_CITED" in wrong["failures"]
    attributed = claim.replace("第二项载明", "第二项（PDF第5页右半页，印刷页码为5）载明")
    assert flow.check(answer(item(attributed, scan), item(identification, title)), [scan, title])["passed"]
    wrong_page = attributed.replace("印刷页码为5", "印刷页码为6")
    assert "SCAN_PAGE_NOT_CITED" in flow.check(answer(item(wrong_page, scan), item(identification, title)),
                                                 [scan, title])["failures"]
    copied_year = flow.check(answer(item("2年内可办理。", scan), item(identification, title)), [scan, title])
    assert "A1_QUANTITY_NOT_QUOTED:2年" in copied_year["failures"]


def test_unverified_business_text_is_removed_from_stops_and_gaps():
    evidence = unit("A", "费用20元。")
    text = "缺少证据，但邮寄费为60元。"
    stop = {"outcome": "stop", "answer": text, "assertions": [], "stop_reason": text, "unknowns": [text]}
    stopped = flow.settle_draft(stop, [evidence], [evidence])["final"]
    assert "60元" not in json.dumps(stopped, ensure_ascii=False)
    good = flow.settle_draft(answer(item("费用20元", evidence), unknowns=[text, "邮费"]), [evidence], [evidence])
    assert good["result_kind"] == "answer_with_gaps" and good["final"]["unknowns"] == [flow.UNVERIFIED_GAP, "邮费"]
    rejected = flow.settle_draft(answer(item("费用60元", evidence, "不存在的引文")), [evidence], [evidence])
    assert rejected["result_kind"] == "stop" and rejected["final"]["unknowns"] == []


def test_first_issue_punctuation_cannot_hide_the_historic_year_requirement():
    fee = unit(FIRST_ISSUE_FEE[0], "首次申领工本费20元。", FIRST_ISSUE_FEE[1])
    waiver = unit(FIRST_ISSUE_WAIVER[0], "自2018年4月1日起停征首次申领工本费。", FIRST_ISSUE_WAIVER[1])
    draft = answer(item("首次申领，工本费20元", fee), item(waiver["text"], waiver))
    assert "FIRST_ISSUE_OLD_FEE_DOCUMENT_YEAR_REQUIRED" in flow.check(draft, [fee, waiver])["failures"]


def test_last_check_completes_an_asked_first_issue_scope_from_the_waiver_quote(tmp_path):
    waiver = unit(FIRST_ISSUE_WAIVER[0], "一、自2018年4月1日起，停征首次申领居民身份证工本费。", FIRST_ISSUE_WAIVER[1])
    question = "仅看河南归档：2018年停征的范围是否包括到期换领和遗失补领？"
    draft = answer(item("自2018年4月1日起停征首次申领居民身份证工本费。", waiver))
    result = flow.ask(question, [waiver], lambda _: {"output": draft})
    assert result["final_checks"]["passed"]
    assert "未将到期换领、遗失补领列入" in result["final"]["answer"]
    service = FlowService([waiver], "test-corpus", tmp_path)
    opened = service.open(question)
    submitted = service.submit(opened["task_id"], draft)
    assert submitted["final_checks"]["passed"] and submitted["final"] == result["final"]

    unrelated = flow.ask("河南归档规定何日起停征首次申领工本费？", [waiver], lambda _: {"output": draft})
    assert len(unrelated["final"]["assertions"]) == 1


def test_failed_model_attempt_is_recorded_without_echoing_the_exception():
    def broken(messages):
        raise RuntimeError("synthetic secret")
    result = flow.ask("河南归档工本费是多少？", [unit("A", "工本费20元。")], broken)
    assert result["stop_origin"] == "MODEL_OR_TOOL_ERROR"
    assert [c["outcome"] for c in result["model"]["calls"]] == ["error"]
    assert "synthetic secret" not in json.dumps(result)


def test_tasks_are_bound_to_runtime_configuration(tmp_path):
    u = unit("A", "办理工本费20元。")
    service = FlowService([u], "test-sha", tmp_path)
    task_id = service.open("办理工本费是多少？")["task_id"]
    draft = answer(item("办理工本费20元", u))
    changed = FlowService([u], "test-sha", tmp_path, follow_up_after_answer=True)
    assert changed.submit(task_id, draft)["error"] == "TASK_RUNTIME_BINDING_MISMATCH"
    path = tmp_path / (task_id + ".json")
    saved = json.loads(path.read_text("utf-8"))
    saved.pop("binding")
    path.write_text(json.dumps(saved), "utf-8")
    assert service.submit(task_id, draft)["error"] == "TASK_RUNTIME_BINDING_MISMATCH"


def test_native_entry_checks_scope_before_creating_a_conversation(monkeypatch):
    def unexpected():
        pytest.fail("Must not initiate a Nexent runtime session")
    monkeypatch.setattr(sys, "argv", ["native", "我的证件号是000000 00000000 0000，可以办吗？"])
    monkeypatch.setattr(runner, "NexentAPI", unexpected)
    assert runner.main() == 2


def test_native_receipt_requires_final_content_order_and_verified_service_result(tmp_path, monkeypatch):
    config = native.runner()
    with pytest.raises(ValueError, match="NATIVE_FINAL_ANSWER_REQUIRED"):
        config.validate_run(config.summarize([]), config.EXPECTED_TOOLS)
    start = {"type": "model_attempt_control", "phase": "begin"}
    opened = {"type": "tool", "tool_name": "govkb_flow_open"}
    submitted = {"type": "tool", "tool_name": "govkb_flow_submit"}
    final = {"type": "final_answer", "content": "最终文本"}
    final_tool = {"type": "tool", "tool_name": "final_answer"}
    for events in ([start, opened, submitted, final],
                   [start, opened, submitted, final_tool, final]):
        config.validate_run(config.summarize(events), config.EXPECTED_TOOLS)
    for events in ([start, submitted, opened, final],
                   [start, opened, final, submitted],
                   [start, opened, final_tool, final],
                   [start, opened, submitted, final_tool, final_tool, final]):
        with pytest.raises(ValueError, match="NATIVE_TOOL_ORDER_MISMATCH"):
            config.validate_run(config.summarize(events), config.EXPECTED_TOOLS)
    tasks = tmp_path / "flow-tasks"
    tasks.mkdir()
    monkeypatch.setattr(native, "RUNTIME", tmp_path)
    final = answer(item("工本费20元", unit("A", "工本费20元。")))
    task = {"question": "测试题", "final": final, "result_kind": "answer", "final_checks": {"passed": True}}
    (tasks / "task.json").write_text(json.dumps(task, ensure_ascii=False), "utf-8")
    with pytest.raises(ValueError, match="NATIVE_FINAL_DIFFERS_FROM_SERVICE"):
        config.VERIFY_FINAL("测试题", "改为工本费40元", set())
    config.VERIFY_FINAL("测试题", flow.native_display_text(final, "answer"), set())


def test_shareable_native_receipts_omit_private_text_and_raw_events():
    sample = {"passed": False, "question": "raw", "final_answer": "raw", "usage_events": [{"raw": True}],
              "tool_order": ["govkb_frozen_retrieve", "raw_secret_like_text"]}
    assert runner.public_receipt(sample) == {"passed": False, "tool_order": ["govkb_frozen_retrieve"], "other_tool_events": 1}
    reports = Path(__file__).resolve().parents[1] / "evidence/frozen-qa-20261030"
    for name in ("native-run-20261030T133155Z.json", "native-kb-run-20261105T010357Z.json"):
        assert not {"question", "final_answer", "usage_events", "conversation_id"} & set(
            json.loads((reports / name).read_text("utf-8")))


def test_same_locator_changed_evidence_cannot_silently_replay():
    first, new = unit("A", "费用20元。"), unit("A", "费用30元。")
    record = {"retrieval": {"evidence": [first]}}
    key = ("A", first["locator"])
    assert diagnostics.rebuild_pool(record, {key: first}) == [first]
    with pytest.raises(ValueError, match="REPLAY_EVIDENCE_CHANGED_OR_UNPINNED"):
        diagnostics.rebuild_pool(record, {key: new})
    assert diagnostics.rebuild_pool(record, {key: new}, allow_corpus_drift=True) == [new]


def test_fact_cards_bind_duplicates_by_address_and_refuse_duplicate_marks():
    header = unit("T", "项目名称 | 设立依据 | 收费标准", "Excel工作表T!第1行", "xlsx_row",
                  cells=["A1", "B1", "C1"])
    row = unit("T", "测试项目 | 20元 | 20元", "Excel工作表T!第2行", "xlsx_row", cells=["A2", "B2", "C2"])
    extracted = cards.extract([header, row])
    cells = {c["value"]["cell"]: c for c in extracted if c["type"] == "table_cell"}
    assert cells["B2"]["span"] != cells["C2"]["span"] and cards.verify_cards(extracted, [header, row]) == []
    assert cards.verify_cards([{**cells["C2"], "span": cells["B2"]["span"]}], [row]) == [cells["C2"]["card_id"]]
    one = {"card_id": cells["B2"]["card_id"], "correct": "是"}
    problems, counts = cards.score([one, one], extracted)
    assert problems and counts["table_cell"]["checked"] == 1

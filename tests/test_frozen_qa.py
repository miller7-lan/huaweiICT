"""Frozen-corpus Q&A: shared retrieval, gates, verification and blind review (synthetic data)."""
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

import build_frozen_corpus as corpus_builder
import generate_unit_candidates as candidates
from gov_mcp import frozen_qa


UNITS = [
    {"source_id": "DOC-A", "code": "A", "locator": "第一条", "text": "申请换领的，交验居民身份证。", "text_sha256": "a"},
    {"source_id": "DOC-B", "code": "B", "locator": "正文段落1",
     "text": "办理时限由25个自然日缩短至15个工作日。", "text_sha256": "b"},
    {"source_id": "DOC-C", "code": "C", "locator": "正文段落2", "text": "与本问题无关的说明文字。", "text_sha256": "c"},
]


def answer(answer_text, *assertions, outcome="answer", reason=""):
    return {"outcome": outcome, "answer": answer_text, "stop_reason": reason, "unknowns": [],
            "assertions": [{"claim": claim, "source_id": source, "locator": locator, "quote": quote}
                           for claim, source, locator, quote in assertions]}


def fake_chat(output, calls=None):
    def chat(messages):
        if calls is not None:
            calls.append(messages)
        return {"output": output, "model": "fake", "usage": {"input_tokens": 10, "output_tokens": 5}}
    return chat


def test_retrieval_is_shared_deterministic_and_drops_zero_scores():
    first = frozen_qa.retrieve(UNITS, "换领交验什么证件")
    assert first[0]["source_id"] == "DOC-A"
    assert first == frozen_qa.retrieve(UNITS, "换领交验什么证件")
    assert all(item["score"] > 0 for item in first)


def test_verified_answer_passes_for_both_methods():
    good = answer("换领时交验居民身份证。", ("交验居民身份证", "DOC-A", "第一条", "申请换领的，交验居民身份证"))
    for method in frozen_qa.METHODS:
        record = frozen_qa.ask("换领交验什么", UNITS, method, fake_chat(good))
        assert record["checks"]["passed"] and record["final"] == good
        assert record["status"]["business_conclusion_status"] == "PAUSED_UNREVIEWED"


@pytest.mark.parametrize("quoted, claimed, should_pass", [
    ("200", "20", False),
    ("25.5", "25", False),
    ("20", "-20", False),
    ("-20", "20", False),
    ("20", "−20", False),
    ("1,000", "1", False),
    ("1,000", "1000", False),
    ("0.5", ".5", False),
    ("20", "20", True),
    ("25.5", "25.5", True),
    ("-20", "-20", True),
    ("−20", "−20", True),
    ("1,000", "1,000", True),
    (".5", ".5", True),
])
def test_numeric_citation_requires_complete_number_token(quoted, claimed, should_pass):
    units = [{"source_id": "SYNTHETIC", "locator": "P1", "text": f"测试费用为{quoted}元。"}]
    candidate = answer(f"测试费用为{claimed}元。",
                       (f"费用{claimed}元", "SYNTHETIC", "P1", f"测试费用为{quoted}元。"))
    result = frozen_qa.verify(candidate, units)
    assert result["passed"] is should_pass
    if not should_pass:
        assert f"NUMBER_NOT_QUOTED:{claimed}" in result["failures"]


def test_numeric_tokens_are_not_created_by_concatenating_distinct_quotes():
    units = [{"source_id": "A", "locator": "P1", "text": "12"},
             {"source_id": "B", "locator": "P2", "text": "34"}]
    candidate = answer("数值为1234。", ("数值", "A", "P1", "12"), ("来源", "B", "P2", "34"))
    result = frozen_qa.verify(candidate, units)
    assert not result["passed"]
    assert "NUMBER_NOT_QUOTED:1234" in result["failures"]


def test_numeric_tokens_are_checked_separately_in_answer_and_claims():
    units = [{"source_id": "A", "locator": "P1", "text": "12元和34元"}]
    candidate = answer("12", ("34元", "A", "P1", "12元和34元"))
    assert frozen_qa.verify(candidate, units)["passed"]


def test_mcp_verifier_turns_numeric_substring_mismatch_into_stop():
    from gov_mcp.frozen_qa_server import FrozenQAService

    units = [{"source_id": "SYNTHETIC", "locator": "P1", "text": "测试费用为200元。"}]
    candidate = answer("测试费用为20元。", ("测试费用20元", "SYNTHETIC", "P1", "测试费用为200元。"))
    result = FrozenQAService(units, "0" * 64).verify("测试费用", candidate)
    assert result["status"] == "OK"
    assert "NUMBER_NOT_QUOTED:20" in result["checks"]["failures"]
    assert result["final"]["outcome"] == "stop"
    assert result["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"


@pytest.mark.parametrize("bad, failure", [
    (answer("交验护照。", ("交验护照", "DOC-A", "第一条", "交验护照")), "A1_QUOTE_NOT_IN_SOURCE"),
    (answer("交验居民身份证。", ("x", "DOC-Z", "第九条", "交验居民身份证")), "A1_CITATION_NOT_RETRIEVED"),
    (answer("缩短10天。", ("缩短", "DOC-B", "正文段落1", "由25个自然日缩短至15个工作日")), "NUMBER_NOT_QUOTED:10"),
    (answer("15个工作日。", ("时限", "DOC-A", "第一条", "交验居民身份证")), "DAY_UNIT_NOT_QUOTED:工作日"),
    (answer("可以办理。"), "ANSWER_WITHOUT_ASSERTIONS"),
])
def test_proposed_turns_failed_checks_into_stop_while_baseline_only_records_them(bad, failure):
    question = "换领办理时限和交验证件"
    proposed = frozen_qa.ask(question, UNITS, "proposed", fake_chat(bad))
    baseline = frozen_qa.ask(question, UNITS, "baseline", fake_chat(bad))
    assert failure in proposed["checks"]["failures"]
    assert proposed["final"]["outcome"] == "stop" and proposed["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert baseline["final"] == bad and failure in baseline["checks"]["failures"]


@pytest.mark.parametrize("question, gate", [
    ("现在换领身份证要多少钱", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("江苏户籍在河南怎么换领", "OUTSIDE_HENAN_SCOPE"),
    ("我的身份证号41010119900101123X能换吗", "PRIVATE_FACT_NOT_ACCEPTED"),
])
def test_proposed_gates_stop_before_any_model_call(question, gate):
    calls = []
    record = frozen_qa.ask(question, UNITS, "proposed", fake_chat(None, calls))
    assert record["gate"] == gate and record["final"]["outcome"] == "stop" and not calls
    assert record["model"]["called"] is False
    baseline_calls = []
    frozen_qa.ask(question, UNITS, "baseline", fake_chat(answer("略", outcome="stop", reason="r"), baseline_calls))
    assert len(baseline_calls) == 1


def test_model_errors_count_as_failed_answers():
    def broken(messages):
        raise RuntimeError("DEEPSEEK_OUTPUT_CONTRACT_VIOLATION")
    record = frozen_qa.ask("换领交验什么", UNITS, "proposed", broken)
    assert record["final"]["stop_reason"] == "MODEL_ERROR" and record["checks"] is None


def test_prompts_differ_only_by_the_proposed_rules():
    evidence = frozen_qa.retrieve(UNITS, "换领")
    base, proposed = (frozen_qa.build_messages("换领", evidence, method) for method in frozen_qa.METHODS)
    assert base[1] == proposed[1]
    assert proposed[0]["content"].startswith(base[0]["content"]) and "不得互相套用" in proposed[0]["content"]


@pytest.mark.parametrize("questions", [
    [{"id": "Q1", "question": "问题", "expected": "答案"}],
    [{"id": "Q1", "question": "问题", "reference": "x"}],
    [{"id": "Q1", "question": "a"}, {"id": "Q1", "question": "b"}],
    [{"id": "Q1", "question": " "}],
    [],
])
def test_questions_file_rejects_answers_duplicates_and_blanks(questions):
    with pytest.raises(ValueError):
        frozen_qa.validate_questions(questions)


def test_blind_review_hides_methods_and_is_reproducible():
    records = []
    for qid in ("Q1", "Q2", "Q3", "Q4"):
        for method in frozen_qa.METHODS:
            final = answer(method, outcome="stop", reason=method)
            records.append({"question_id": qid, "question": "问" + qid, "method": method, "final": final})
    items, key = frozen_qa.blind(records, "seed")
    assert (items, key) == frozen_qa.blind(records, "seed")
    assert all("method" not in json.dumps(item) and set(item) == {"id", "question", "A", "B"} for item in items)
    for item in items:
        assert item["A"]["answer"] == key[item["id"]]["A"] and item["B"]["answer"] == key[item["id"]]["B"]
    with pytest.raises(ValueError, match="INCOMPLETE_PAIR"):
        frozen_qa.blind(records[:-1], "seed")


def test_method_order_is_seeded_per_question():
    orders = {qid: frozen_qa.method_order(qid, "s") for qid in ("Q%d" % i for i in range(12))}
    assert orders == {qid: frozen_qa.method_order(qid, "s") for qid in orders}
    assert {tuple(order) for order in orders.values()} == {tuple(frozen_qa.METHODS), tuple(reversed(frozen_qa.METHODS))}


def test_html_articles_keep_continuation_lines_and_catalog_drift_stops():
    html = ("<div id='content'><p>总则说明文字</p><p>第一条 公民应当申领。</p><p>（一）年满十六周岁；</p>"
            "<p>第二章 换领</p><p>第二条 换领交验旧证。</p></div>").encode()
    spec = {"code": "X", "source_id": "DOC-X", "selector": {"id": "content"}, "min_text_length": 1, "articles": True}
    units = corpus_builder.html_units(spec, html)
    assert [(u["locator"], u["text"]) for u in units] == [
        ("正文段落1", "总则说明文字"), ("第一条", "第一条 公民应当申领。\n（一）年满十六周岁；"),
        ("正文段落4", "第二章 换领"), ("第二条", "第二条 换领交验旧证。")]
    document = {"source_id": "DOC-X", "selected_clauses": [{"locator": "第一条", "text_sha256": units[1]["text_sha256"]}]}
    assert corpus_builder.check_catalog(units, document) == 1
    document["selected_clauses"][0]["text_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="CATALOG_CLAUSE_DRIFT"):
        corpus_builder.check_catalog(units, document)


def test_xlsx_rows_keep_literals_and_skip_formulas():
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buffer = BytesIO()
    with ZipFile(buffer, "w") as book:
        book.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="收费" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr("xl/sharedStrings.xml", f"<sst {ns}><si><t>居民身份证工本费</t></si></sst>")
        book.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="2">'
                      '<c r="A2" t="s"><v>0</v></c><c r="B2"><v>20</v></c><c r="C2"><f>B2*2</f><v>40</v></c>'
                      '</row></sheetData></worksheet>')
    units, formulas = corpus_builder.xlsx_units({"code": "X", "source_id": "DOC-X"}, buffer.getvalue())
    assert formulas == 1 and [(u["locator"], u["text"], u["cells"]) for u in units] == [
        ("Excel工作表收费!第2行", "居民身份证工本费 | 20", ["A2", "B2"])]


def test_candidate_quotes_are_checked_against_unit_text():
    texts = [{"text": "申请换领的，交验居民身份证。"}]
    checked = candidates.check([{"kind": "material", "subject": "换领", "predicate": "requires", "object": "居民身份证",
                                 "qualifiers": [], "quote": "交验居民 身份证"},
                                {"kind": "material", "subject": "换领", "predicate": "requires", "object": "护照",
                                 "qualifiers": [], "quote": "交验护照"}], texts)
    assert [item["quote_in_source"] for item in checked] == [True, False]


def test_unit_resolution_stops_on_missing_locator_or_changed_text():
    corpus = [{"source_id": "DOC-A", "locator": "第一条", "text": "t", "text_sha256": "h1"}]
    unit = {"id": "U1", "sources": [{"source_id": "DOC-A", "locators": [{"locator": "第一条", "text_sha256": "h1"}]}]}
    assert candidates.resolve(unit, corpus) == corpus
    unit["sources"][0]["locators"][0]["text_sha256"] = "other"
    with pytest.raises(ValueError, match="UNIT_TEXT_CHANGED"):
        candidates.resolve(unit, corpus)
    unit["sources"][0]["locators"][0]["locator"] = "第九条"
    with pytest.raises(ValueError, match="UNIT_LOCATOR_NOT_IN_CORPUS"):
        candidates.resolve(unit, corpus)


def test_mcp_service_retrieves_gates_and_verifies_like_the_proposed_method():
    from gov_mcp.frozen_qa_server import FrozenQAService, create_app
    service = FrozenQAService(UNITS, "c" * 64)
    ok = service.retrieve("换领交验什么证件")
    assert ok["status"] == "OK" and ok["evidence"][0]["source_id"] == "DOC-A"
    assert "不得互相套用" in ok["answer_rules"] and ok["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    stop = service.retrieve("我的身份证号41010119900101123X能换吗")
    assert stop["status"] == "STOP" and stop["evidence"] == []
    good = answer("换领时交验居民身份证。", ("交验居民身份证", "DOC-A", "第一条", "申请换领的，交验居民身份证"))
    assert service.verify("换领交验什么证件", good)["final"] == good
    bad = answer("交验护照。", ("交验护照", "DOC-A", "第一条", "交验护照"))
    checked = service.verify("换领交验什么证件", bad)
    assert checked["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert service.verify("换领", {"outcome": "answer"})["error"] == "ANSWER_CONTRACT_VIOLATION"
    assert service.verify("换领", {**good, "outcome": "maybe"})["error"] == "ANSWER_CONTRACT_VIOLATION"
    with pytest.raises(ValueError, match="DEDICATED_TOKEN_REQUIRED"):
        create_app(service, "short")


def test_positional_references_are_not_treated_as_unquoted_facts():
    row = {"source_id": "DOC-X", "locator": "Excel工作表收费!第36行", "text": "居民身份证工本费 | 财综〔2007〕34号"}
    para = {"source_id": "DOC-Y", "locator": "正文段落4", "text": "办理时限为15个工作日。"}
    cited = answer("目录第36行列明的依据为财综〔2007〕34号。", ("依据", "DOC-X", row["locator"], "财综〔2007〕34号"))
    assert frozen_qa.verify(cited, [row, para])["passed"]
    # A fact number must still be quoted even when it equals a digit inside the cited locator.
    wrong = answer("办理时限为4个工作日。", ("时限", "DOC-Y", "正文段落4", "办理时限为15个工作日"))
    assert "NUMBER_NOT_QUOTED:4" in frozen_qa.verify(wrong, [row, para])["failures"]
    page = answer("扫描件第5页写明为15个工作日。", ("时限", "DOC-Y", "正文段落4", "办理时限为15个工作日"))
    assert frozen_qa.verify(page, [row, para])["passed"]


def test_whole_corpus_verification_accepts_units_outside_the_literal_top_five():
    from gov_mcp.frozen_qa_server import FrozenQAService
    service = FrozenQAService(UNITS, "c" * 64)
    # DOC-C does not rank for this question, so only whole-corpus verification may cite it.
    cited = answer("说明文字如下。", ("说明", "DOC-C", "正文段落2", "与本问题无关的说明文字"))
    assert service.verify("换领交验什么证件", cited)["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert service.verify("换领交验什么证件", cited, whole_corpus=True)["final"] == cited
    forged = answer("交验护照。", ("交验护照", "DOC-A", "第一条", "交验护照"))
    assert service.verify("换领", forged, whole_corpus=True)["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert service.verify("现在换领要多少钱", cited, whole_corpus=True)["status"] == "STOP"


def test_source_metadata_is_cited_by_date_role_and_cannot_support_policy_numbers():
    from gov_mcp.frozen_qa_server import FrozenQAService

    spec = {"source_id": "DOC-A", "code": "A", "publication": "visible_and_meta"}
    raw = ('<meta name="ArticleTitle" content="河南2026年便民服务措施">'
           '<meta name="PubDate" content="2026-05-05 09:18:04">'
           '<meta name="lastmodifiedtime" content="2026-05-11 09:23">'
           '<i id="pubDate">2026-05-05</i>').encode()
    metadata = corpus_builder.html_metadata_units(spec, raw)
    assert [unit["evidence_kind"] for unit in metadata] == [
        "source_title", "page_publication_date", "page_modified_date"]
    mismatch = raw.replace(b'>2026-05-05<', b'>2026-05-11<')
    assert "page_publication_date" not in {
        unit["evidence_kind"] for unit in corpus_builder.html_metadata_units(spec, mismatch)
    }
    body = {**UNITS[1], "source_id": "DOC-A"}
    service = FrozenQAService([body, *metadata], "c" * 64)
    date_only = service.retrieve("河南2026年文件的网页发布日期是什么")["evidence"]
    assert any(unit["locator"] == metadata[1]["locator"] for unit in date_only)
    question = "比较2026年文件的网页发布日期、修改日期和办理时限"
    citations = [
        ("办理时限为15个工作日", "DOC-A", body["locator"], "15个工作日"),
        *(("", "DOC-A", unit["locator"], unit["text"]) for unit in metadata),
    ]
    valid = "2026年文件的网页发布日期为2026年5月5日，网页最后修改日期为2026年5月11日。"
    assert service.verify(question, answer(valid, *citations))["final"]["outcome"] == "answer"
    for invalid in ("网页发布日期为2026年5月11日。", "2026年5月5日为施行日期。",
                    "办理期限为2026年。", "办理时限为11个工作日。"):
        assert service.verify(question, answer(invalid, *citations))["final"]["outcome"] == "stop"
    heading = {**UNITS[0], "locator": "文头", "text": "2026年5月5日 财综〔2026〕11号"}
    bad_fee = answer("办理费为11元。", ("文件文号", heading["source_id"], heading["locator"], heading["text"]))
    assert not frozen_qa.verify(bad_fee, [heading])["passed"]
    other = {**UNITS[0], "source_id": "DOC-B"}
    swapped = answer(valid, *citations, ("发布于2026年5月5日", "DOC-B", other["locator"], other["text"]))
    assert not frozen_qa.verify(swapped, [body, other, *metadata])["passed"]


def test_retrieval_links_comparison_sources_and_only_the_matching_table_header():
    from gov_mcp.frozen_qa_server import FrozenQAService

    units = [
        corpus_builder.unit({"source_id": "NEW", "code": "N"}, f"P{i}",
                            "居民身份证办理时限和收费规定说明", "html_paragraph")
        for i in range(5)
    ]
    for source, year in (("NEW", 2026), ("OLD", 2024)):
        units.append(corpus_builder.unit({"source_id": source, "code": "T"}, "标题",
                                        f"河南公安机关{year}年便民服务措施", "source_title"))
    units.append(corpus_builder.unit({"source_id": "OLD", "code": "O"}, "P1", "办理时限为25个自然日",
                                    "html_paragraph"))
    table = {"source_id": "TABLE", "code": "T"}
    header = corpus_builder.unit(table, "Excel工作表收费!第4行", "项目名称 | 设立依据 | 收费标准",
                                 "xlsx_row", cells=["D4", "F4", "G4"])
    wrong_header = {**header, "locator": "Excel工作表考试!第4行"}
    row = corpus_builder.unit(table, "Excel工作表收费!第36行", "居民身份证收费标准 | 20元",
                              "xlsx_row", cells=["D36", "G36"])
    service = FrozenQAService([*units, header, wrong_header, row], "c" * 64)
    compare = service.retrieve("比较2024年和2026年居民身份证办理时限和收费规定")["evidence"]
    assert ("OLD", "P1") in {(unit["source_id"], unit["locator"]) for unit in compare}
    table_question = "居民身份证收费标准"
    results = {unit["locator"]: unit for unit in service.retrieve(table_question)["evidence"]}
    assert results[header["locator"]]["cells"] == ["D4", "F4", "G4"]
    assert wrong_header["locator"] not in results
    cited = answer("表格列有收费标准，相关一行记载20元。",
                   ("收费标准表头", "TABLE", header["locator"], "收费标准"),
                   ("费用为20元", "TABLE", row["locator"], "20元"))
    assert service.verify(table_question, cited)["final"] == cited


def test_compound_question_retrieves_fee_and_retake_evidence():
    question = "派出所给办身份证拍照，要不要另外交照相费？对拍出来的照片不满意，能不能重拍？"
    units = [{"source_id": "PHOTO", "locator": f"P{i}",
              "text": "派出所给办身份证拍照，对拍出来的照片不满意，可以重新拍照。"} for i in range(5)]
    units.append({"source_id": "FEE", "locator": "P1", "text": "立即纠正收取照相费等违规收费行为。"})
    assert {hit["source_id"] for hit in frozen_qa.retrieve(units, question)} == {"PHOTO"}
    hits = frozen_qa.retrieve_evidence(units, question)
    assert {hit["source_id"] for hit in hits} == {"PHOTO", "FEE"}
    assert len(hits) == 6
    assert len(list(frozen_qa.compound_queries("照相费是多少，照片不满意能不能重拍？"))) == 2


def test_cross_document_quantities_need_the_same_value_and_unit_in_each_claims_quote():
    units = [{"source_id": "OLD", "locator": "P1", "text": "普通办理为25个自然日。"},
             {"source_id": "NEW", "locator": "P1", "text": "普通办理为15个工作日。"}]
    old, new = units
    good = answer("旧资料为25个自然日，新资料为15个工作日。",
                  ("旧资料为25个自然日", "OLD", "P1", old["text"]),
                  ("新资料为15个工作日", "NEW", "P1", new["text"]))
    assert frozen_qa.verify(good, units)["passed"]
    swapped = answer("旧资料为15个自然日，新资料为25个工作日。",
                     ("旧资料为15个自然日", "OLD", "P1", old["text"]),
                     ("新资料为25个工作日", "NEW", "P1", new["text"]))
    assert "A1_QUANTITY_NOT_QUOTED:15自然日" in frozen_qa.verify(swapped, units)["failures"]
    mixed = answer("旧资料为15个工作日，新资料为25个自然日。",
                   ("旧资料为15个工作日", "OLD", "P1", old["text"]),
                   ("新资料为25个自然日", "NEW", "P1", new["text"]))
    assert "A1_QUANTITY_NOT_QUOTED:15工作日" in frozen_qa.verify(mixed, units)["failures"]
    summary_only = {**good, "answer": swapped["answer"]}
    assert "ANSWER_QUANTITY_NOT_QUOTED:15自然日" in frozen_qa.verify(summary_only, units)["failures"]


def test_verified_cross_document_answer_is_built_from_claims_in_mcp_and_cli():
    from gov_mcp.frozen_qa_server import FrozenQAService

    units = [
        {"source_id": "OLD", "locator": "P1", "text": "普通办理为25个自然日。", "text_sha256": "a"},
        {"source_id": "NEW", "locator": "P1", "text": "普通办理为15个工作日。", "text_sha256": "b"},
    ]
    candidate = answer("旧资料为15个工作日，新资料为25个自然日。",
                       ("旧资料为25个自然日", "OLD", "P1", units[0]["text"]),
                       ("新资料为15个工作日", "NEW", "P1", units[1]["text"]))
    candidate["unknowns"] = ["现行效力待核"]
    question = "比较普通办理时限"
    expected = {**candidate, "answer": "- 旧资料为25个自然日\n- 新资料为15个工作日"}
    service = FrozenQAService(units, "0" * 64)
    assert service.verify(question, candidate)["final"] == expected
    assert service.verify(question, candidate, whole_corpus=True)["final"] == expected
    assert frozen_qa.ask(question, units, "proposed", fake_chat(candidate))["final"] == expected
    assert frozen_qa.ask(question, units, "baseline", fake_chat(candidate))["final"] == candidate
    missing = {**candidate, "assertions": [candidate["assertions"][0], {**candidate["assertions"][1], "claim": ""}]}
    result = service.verify(question, missing)
    assert result["final"]["outcome"] == "stop"
    assert "ANSWER_QUANTITY_WITHOUT_CLAIM:15工作日" in result["checks"]["failures"]

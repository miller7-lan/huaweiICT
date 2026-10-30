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
    assert service.verify("换领交验什么证件", json.dumps(good, ensure_ascii=False))["final"] == good
    bad = answer("交验护照。", ("交验护照", "DOC-A", "第一条", "交验护照"))
    checked = service.verify("换领交验什么证件", json.dumps(bad, ensure_ascii=False))
    assert checked["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert service.verify("换领", '{"outcome": "answer"}')["error"] == "ANSWER_JSON_CONTRACT_VIOLATION"
    with pytest.raises(ValueError, match="DEDICATED_TOKEN_REQUIRED"):
        create_app(service, "short")

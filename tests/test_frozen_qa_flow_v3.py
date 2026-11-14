"""Bounded cross-document flow v3: titles, follow-up, repair and claim salvage (synthetic data)."""
from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow


def unit(source, locator, text, kind="html_paragraph"):
    return {"source_id": source, "code": source, "locator": locator, "text": text,
            "text_sha256": frozen_qa.sha(text), "evidence_kind": kind}


UNITS = [
    unit("DOC-RENEW", "网页标题", "2019年换领收费通知", "source_title"),
    unit("DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元，办理时限60个工作日。"),
    unit("DOC-AGENT", "正文段落1", "申领居民身份证必须本人办理，不得代办。"),
    unit("DOC-TEMP", "正文段落1", "临时居民身份证有效期限为三个月。"),
    unit("DOC-OTHER", "正文段落1", "户口迁移需提交准予迁入证明。"),
]
QUESTION = "到期换领的工本费是多少？"
RENEW = ("到期换领工本费20元", "DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元")
WRONG = ("到期换领工本费60元", "DOC-RENEW", "正文段落1", "工本费20元")
TEMP = ("临时居民身份证有效期限为三个月", "DOC-TEMP", "正文段落1", "临时居民身份证有效期限为三个月")


def answer(text, *assertions, outcome="answer", reason="", unknowns=()):
    return {"outcome": outcome, "answer": text, "stop_reason": reason, "unknowns": list(unknowns),
            "assertions": [dict(zip(("claim", "source_id", "locator", "quote"), item)) for item in assertions]}


def scripted(*outputs):
    calls = []

    def chat(messages):
        calls.append(messages)
        return {"output": outputs[len(calls) - 1], "model": "fake", "usage": None}
    chat.calls = calls
    return chat


def claims(record):
    return [item["claim"] for item in record["final"]["assertions"] if not item["claim"].startswith(flow.TITLE_NOTE)]


def test_verified_answer_takes_one_call_and_evidence_markers_are_not_facts():
    record = flow.ask(QUESTION, UNITS, scripted(answer("到期换领工本费20元。[1]", RENEW)))
    assert record["result_kind"] == "answer" and len(record["model"]["calls"]) == 1
    assert "[1]" not in record["final"]["answer"] and record["model_output"]["answer"].endswith("[1]")
    assert record["status"]["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert frozen_qa.verify(record["final"], [dict(u, rank=i) for i, u in enumerate(UNITS, 1)])["passed"]


def test_backend_title_supports_a_document_year_but_not_a_wrong_year():
    named = ("2019年通知记载到期换领工本费20元", "DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元")
    pool = [dict(u, rank=i) for i, u in enumerate(UNITS[1:], 1)]
    assert not frozen_qa.verify(answer("2019年通知记载到期换领工本费20元。", named), pool)["passed"]
    record = flow.ask(QUESTION, UNITS, scripted(answer("2019年通知记载到期换领工本费20元。", named)))
    assert record["result_kind"] == "answer" and len(record["model"]["calls"]) == 1
    assert any(item["claim"].startswith(flow.TITLE_NOTE) for item in record["final"]["assertions"])
    wrong_year = ("2021年通知记载到期换领工本费20元", "DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元")
    record = flow.ask(QUESTION, UNITS, scripted(*[answer("2021年通知记载到期换领工本费20元。", wrong_year)] * 2))
    assert record["result_kind"] == "stop" and record["stop_origin"] == "VERIFICATION_FAILED_AFTER_REPAIR"


def test_failed_check_is_repaired_once():
    chat = scripted(answer("到期换领工本费60元。", WRONG), answer("到期换领工本费20元。", RENEW))
    record = flow.ask(QUESTION, UNITS, chat)
    assert record["result_kind"] == "answer" and record["repairs_used"] == 1 and claims(record) == [RENEW[0]]
    assert [(call["assertions"], call["unknowns"]) for call in record["model"]["calls"]] == [(1, 0), (1, 0)]
    assert "QUANTITY_NOT_QUOTED" in chat.calls[1][-1]["content"]


def test_salvage_keeps_verified_claims_and_never_shows_the_dropped_one():
    mixed = answer("临时证有效期限为三个月；到期换领工本费60元。", TEMP, WRONG)
    record = flow.ask("临时居民身份证有效期限和到期换领的工本费分别是什么？", UNITS, scripted(mixed, mixed))
    # The wrong paraphrase (60元) is replaced by the source sentence it quoted (20元).
    assert record["result_kind"] == "partial_answer"
    assert (record["claims_dropped"], record["claims_excerpted"]) == (0, 1)
    assert claims(record) == [TEMP[0], flow.EXCERPT_NOTE + UNITS[1]["text"]] and "60元" not in json_text(record["final"])
    assert "部分答复" in record["final"]["answer"] and "原文摘录" in record["final"]["answer"]
    # checks describes the last draft; final_checks describes what is displayed.
    assert not record["checks"]["passed"] and record["final_checks"]["passed"]
    pool = [dict(u, rank=i) for i, u in enumerate(UNITS, 1)]
    assert frozen_qa.verify({**record["final"], "answer": TEMP[0]}, pool)["passed"]


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False)


def test_nothing_verifiable_still_stops():
    record = flow.ask(QUESTION, UNITS, scripted(*[answer("到期换领工本费60元。", WRONG)] * 2))
    assert record["final"]["stop_reason"] == "VERIFICATION_FAILED" and len(record["model"]["calls"]) == 2


def test_prohibition_stated_as_permission_is_dropped():
    quote = "申领居民身份证必须本人办理，不得代办"
    wrong = answer("可以委托他人代办。", ("可以委托他人代办", "DOC-AGENT", "正文段落1", quote))
    record = flow.ask("申领居民身份证能否代办？", UNITS, scripted(wrong, wrong))
    assert record["result_kind"] == "stop"
    assert "A1_PROHIBITION_STATED_AS_PERMISSION" in record["model"]["calls"][0]["failures"]


def test_stop_triggers_one_follow_up_retrieval_then_answers():
    chat = scripted(answer("缺少依据。", outcome="stop", reason="证据不足", unknowns=["户口迁移准予迁入证明"]),
                    answer("到期换领工本费20元。", RENEW))
    record = flow.ask(QUESTION, UNITS[:4], chat)
    assert record["result_kind"] == "stop" and record["stop_origin"] == "INSUFFICIENT_EVIDENCE_AFTER_FOLLOW_UP"
    assert len(chat.calls) == 1  # Nothing new was retrieved, so the model is not asked again.
    record = flow.ask(QUESTION, UNITS, chat := scripted(
        answer("缺少依据。", outcome="stop", reason="证据不足", unknowns=["户口迁移准予迁入证明"]),
        answer("到期换领工本费20元。", RENEW)))
    assert record["result_kind"] == "answer" and record["follow_ups_used"] == 1 and len(chat.calls) == 2
    assert [item["kind"] for item in record["retrieval"]["rounds"]] == ["initial", "follow_up"]


def test_a_passing_answer_is_never_replaced_by_a_worse_follow_up():
    first = answer("到期换领工本费20元。", RENEW, unknowns=["户口迁移准予迁入证明"])
    for second in (answer("到期换领工本费60元。", WRONG), answer("缺。", outcome="stop", reason="证据不足")):
        record = flow.ask(QUESTION, UNITS, chat := scripted(first, second, second), follow_up_after_answer=True)
        # The kept answer still names a gap, so it is not counted as a complete answer.
        assert record["result_kind"] == "answer_with_gaps" and claims(record) == [RENEW[0]]
        assert len(chat.calls) == 2 and record["final_checks"]["passed"]  # No repair of the worse draft.


def test_boundary_unknowns_do_not_trigger_a_follow_up():
    record = flow.ask(QUESTION, UNITS, scripted(answer("到期换领工本费20元。", RENEW, unknowns=["现行收费标准是否仍然有效"])))
    assert record["follow_ups_used"] == 0 and len(record["model"]["calls"]) == 1


def test_gates_stop_before_retrieval_and_errors_are_not_missing_evidence():
    chat = scripted()
    for question, gate in (("南京换领工本费是多少", "OUTSIDE_HENAN_SCOPE"), ("现在换领工本费是多少", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
                           ("41010119900101123X 换领工本费", "PRIVATE_FACT_NOT_ACCEPTED")):
        record = flow.ask(question, UNITS, chat)
        assert record["gate"] == gate and record["stop_origin"] == "SCOPE_GATE"
        assert not record["model"]["called"] and record["retrieval"]["evidence"] == []
    assert chat.calls == []

    def broken(messages):
        raise RuntimeError("boom")
    record = flow.ask(QUESTION, UNITS, broken)
    assert record["final"]["stop_reason"] == "MODEL_ERROR" and record["stop_origin"] == "MODEL_OR_TOOL_ERROR"


def test_model_calls_never_exceed_the_bound():
    stop = answer("缺。", outcome="stop", reason="证据不足", unknowns=["户口迁移准予迁入证明"])
    bad = answer("到期换领工本费60元。", WRONG)
    record = flow.ask(QUESTION, UNITS, scripted(stop, bad, bad, bad))
    assert len(record["model"]["calls"]) == flow.MAX_MODEL_CALLS == 3


def test_document_numbers_keep_their_bracketed_year():
    text = "依据财综[2007]34号，见[E2]和 [1]。"
    cleaned = flow.clean(answer(text, (text, "DOC-RENEW", "正文段落1", "工本费20元")))
    assert cleaned["answer"] == cleaned["assertions"][0]["claim"] == "依据财综[2007]34号，见和 。"


def test_backend_heading_supports_a_year_without_showing_its_digits_as_a_fact():
    units = UNITS[1:] + [unit("DOC-RENEW", "正文段落0", "2019年3月5日 豫财综[2019]12号")]
    named = ("2019年通知记载到期换领工本费20元", "DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元")
    record = flow.ask(QUESTION, units, scripted(answer("2019年通知记载到期换领工本费20元。", named)))
    assert record["result_kind"] == "answer" and record["final_checks"]["passed"]
    note = next(item for item in record["final"]["assertions"] if item["claim"].startswith(flow.TITLE_NOTE))
    assert "2019" not in note["claim"] and "2019" in note["quote"]


def test_backend_citations_are_not_added_to_a_draft_that_already_passes():
    record = flow.ask(QUESTION, UNITS, scripted(answer("到期换领工本费20元。", RENEW)))
    assert claims(record) == [item["claim"] for item in record["final"]["assertions"]]


def test_a_claim_with_an_invalid_citation_is_dropped_not_excerpted():
    invented = ("到期换领工本费60元", "DOC-RENEW", "正文段落1", "工本费60元")
    mixed = answer("临时证有效期限为三个月；到期换领工本费60元。", TEMP, invented)
    record = flow.ask("临时居民身份证有效期限和到期换领的工本费分别是什么？", UNITS, scripted(mixed, mixed))
    assert record["result_kind"] == "partial_answer" and claims(record) == [TEMP[0]]
    assert (record["claims_dropped"], record["claims_excerpted"]) == (1, 0) and record["final"]["unknowns"]


def test_excerpts_alone_never_make_an_answer():
    record = flow.ask(QUESTION, UNITS, scripted(*[answer("到期换领工本费60元。", WRONG)] * 2))
    assert record["result_kind"] == "stop" and record["claims_excerpted"] == 0


def test_excerpt_is_the_whole_sentence_of_a_long_unit():
    text = "甲" * 150 + "。申请人有条件的，可以当天领取临时证，工本费10元。" + "乙" * 150 + "。"
    assert flow.sentence_around(text, "工本费10元") == "申请人有条件的，可以当天领取临时证，工本费10元。"
    assert flow.sentence_around("短句，工本费10元。", "工本费10元") == "短句，工本费10元。"


def test_an_answer_with_gaps_is_returned_without_a_second_call_by_default():
    first = answer("到期换领工本费20元。", RENEW, unknowns=["户口迁移准予迁入证明"])
    record = flow.ask(QUESTION, UNITS, chat := scripted(first))
    assert record["result_kind"] == "answer_with_gaps" and len(chat.calls) == 1 and record["follow_ups_used"] == 0

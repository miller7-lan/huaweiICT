"""Cross-document chain v2: pooled retrieval, follow-up, bound checks and repair (synthetic data)."""
from gov_mcp import frozen_qa, frozen_qa_v2 as v2


FEE = [{"source_id": "DOC-FEE", "code": "F", "locator": f"第{i}条", "text_sha256": f"f{i}",
        "text": f"首次申领居民身份证收费规定第{i}款：首次申领免收工本费。"} for i in range(1, 6)]
UNITS = FEE + [
    {"source_id": "DOC-RENEW", "code": "R", "locator": "正文段落1", "text_sha256": "r1",
     "text": "到期换领居民身份证的，工本费20元，办理时限60个工作日。"},
    {"source_id": "DOC-AGENT", "code": "G", "locator": "正文段落1", "text_sha256": "g1",
     "text": "申领居民身份证必须本人办理，不得代办。"},
    {"source_id": "DOC-TEMP", "code": "T", "locator": "正文段落1", "text_sha256": "t1",
     "text": "临时居民身份证有效期限为三个月。"},
]
QUESTION = "首次申领居民身份证收费规定与到期换领的工本费分别是什么？"
RENEW = ("到期换领工本费20元", "DOC-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元")
FIRST = ("首次申领免收工本费", "DOC-FEE", "第1条", "首次申领免收工本费")


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


def test_pool_reaches_the_second_document_that_single_top_five_misses():
    assert {item["source_id"] for item in frozen_qa.retrieve(UNITS, QUESTION)} == {"DOC-FEE"}
    pool = []
    v2.extend_pool(pool, UNITS, v2.plan_queries(QUESTION))
    sources = [item["source_id"] for item in pool]
    assert "DOC-RENEW" in sources and sources.count("DOC-FEE") <= v2.PER_SOURCE
    assert [item["rank"] for item in pool] == list(range(1, len(pool) + 1))


def test_pool_only_grows_and_keeps_earlier_numbering():
    pool = []
    v2.extend_pool(pool, UNITS, ["到期换领工本费"])
    before = [(item["rank"], item["source_id"], item["locator"]) for item in pool]
    assert v2.extend_pool(pool, UNITS, ["临时居民身份证有效期限"]) >= 1
    assert [(item["rank"], item["source_id"], item["locator"]) for item in pool][:len(before)] == before
    assert v2.extend_pool(pool, UNITS, ["临时居民身份证有效期限"]) == 0


def test_two_document_answer_passes_and_counts_sources():
    record = v2.ask(QUESTION, UNITS, scripted(answer("首次申领免收工本费；到期换领工本费20元。[1]", FIRST, RENEW)))
    assert record["final"]["outcome"] == "answer" and record["checks"]["sources_cited"] == 2
    assert record["stop_origin"] is None and len(record["model"]["calls"]) == 1
    assert record["status"]["business_conclusion_status"] == "PAUSED_UNREVIEWED"


def test_swapped_numbers_fail_although_both_numbers_are_quoted():
    pool = []
    v2.extend_pool(pool, UNITS, ["到期换领工本费办理时限"])
    quote = "到期换领居民身份证的，工本费20元，办理时限60个工作日"
    swapped = answer("工本费60元，时限20个工作日。", ("工本费60元，20个工作日", "DOC-RENEW", "正文段落1", quote))
    assert frozen_qa.verify(swapped, pool)["passed"]  # The first chain compares bare number sets.
    failures = v2.verify(swapped, pool)["failures"]
    assert "A1_CLAIM_QUANTITY_NOT_IN_OWN_QUOTE:60元" in failures and "ANSWER_QUANTITY_NOT_QUOTED:20工作日" in failures
    right = answer("工本费20元，时限60个工作日。", ("工本费20元，60个工作日", "DOC-RENEW", "正文段落1", quote))
    assert v2.verify(right, pool)["passed"]


def test_number_from_another_assertion_does_not_support_a_claim():
    pool = []
    v2.extend_pool(pool, UNITS, v2.plan_queries(QUESTION))
    borrowed = answer("首次申领工本费20元。", ("首次申领工本费20元", "DOC-FEE", "第1条", "首次申领免收工本费"), RENEW)
    assert "A1_CLAIM_QUANTITY_NOT_IN_OWN_QUOTE:20元" in v2.verify(borrowed, pool)["failures"]


def test_prohibition_stated_as_permission_fails():
    pool = []
    v2.extend_pool(pool, UNITS, ["申领居民身份证代办"])
    quote = "申领居民身份证必须本人办理，不得代办"
    wrong = answer("可以委托他人代办。", ("可以委托他人代办", "DOC-AGENT", "正文段落1", quote))
    assert v2.verify(wrong, pool)["failures"] == ["A1_PROHIBITION_STATED_AS_PERMISSION"]
    assert v2.verify(answer("必须本人办理，不得代办。", ("不得代办", "DOC-AGENT", "正文段落1", quote)), pool)["passed"]


def test_first_stop_triggers_one_follow_up_retrieval_then_answers():
    question = "到期换领的工本费是多少？"
    temp = ("临时居民身份证有效期限为三个月", "DOC-TEMP", "正文段落1", "临时居民身份证有效期限为三个月")
    chat = scripted(answer("缺少依据。", outcome="stop", reason="证据不足", unknowns=["临时居民身份证有效期限"]),
                    answer("临时居民身份证有效期限为三个月。", temp))
    record = v2.ask(question, UNITS, chat)
    assert record["final"]["outcome"] == "answer" and record["follow_ups_used"] == 1
    assert [item["kind"] for item in record["retrieval"]["rounds"]] == ["initial", "follow_up"]
    assert "DOC-TEMP" not in chat.calls[0][1]["content"] and "DOC-TEMP" in chat.calls[1][1]["content"]


def test_stop_is_final_when_follow_up_finds_nothing_or_was_already_used():
    stop = answer("缺少依据。", outcome="stop", reason="证据不足", unknowns=["港澳通行证签注费用标准"])
    nothing = v2.ask("到期换领的工本费是多少？", UNITS[5:6], scripted(stop))
    assert nothing["stop_origin"] == "INSUFFICIENT_EVIDENCE_NOTHING_MORE_RETRIEVED" and len(nothing["model"]["calls"]) == 1
    again = answer("仍缺依据。", outcome="stop", reason="证据不足", unknowns=["临时居民身份证有效期限"])
    record = v2.ask("到期换领的工本费是多少？", UNITS, scripted(again, again))
    assert record["final"]["outcome"] == "stop" and record["stop_origin"] == "INSUFFICIENT_EVIDENCE_AFTER_FOLLOW_UP"
    assert len(record["model"]["calls"]) == 2


def test_failed_check_is_repaired_once_then_stops():
    bad = answer("到期换领工本费60元。", ("工本费60元", "DOC-RENEW", "正文段落1", "工本费20元"))
    good = answer("到期换领工本费20元。", RENEW)
    chat = scripted(bad, good)
    record = v2.ask("到期换领的工本费是多少？", UNITS, chat)
    assert record["final"] == good and record["repairs_used"] == 1
    assert "A1_CLAIM_QUANTITY_NOT_IN_OWN_QUOTE:60元" in chat.calls[1][-1]["content"]
    record = v2.ask("到期换领的工本费是多少？", UNITS, scripted(bad, bad))
    assert record["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert record["stop_origin"] == "VERIFICATION_FAILED_AFTER_REPAIR" and len(record["model"]["calls"]) == 2


def test_gates_stop_before_any_model_call_and_errors_are_not_missing_evidence():
    chat = scripted()
    for question, gate in (("南京换领工本费是多少", "OUTSIDE_HENAN_SCOPE"), ("现在换领工本费是多少", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
                           ("41010119900101123X 换领工本费", "PRIVATE_FACT_NOT_ACCEPTED")):
        record = v2.ask(question, UNITS, chat)
        assert record["gate"] == gate and record["stop_origin"] == "SCOPE_GATE" and not record["model"]["called"]
    assert chat.calls == []

    def broken(messages):
        raise RuntimeError("boom")
    record = v2.ask("到期换领的工本费是多少？", UNITS, broken)
    assert record["final"]["stop_reason"] == "MODEL_ERROR" and record["stop_origin"] == "MODEL_OR_TOOL_ERROR"


def test_model_calls_never_exceed_the_bound():
    stop = answer("缺。", outcome="stop", reason="证据不足", unknowns=["临时居民身份证有效期限"])
    bad = answer("到期换领工本费60元。", ("工本费60元", "DOC-RENEW", "正文段落1", "工本费20元"))
    record = v2.ask("到期换领的工本费是多少？", UNITS, scripted(stop, bad, bad))
    assert len(record["model"]["calls"]) == v2.MAX_MODEL_CALLS == 3

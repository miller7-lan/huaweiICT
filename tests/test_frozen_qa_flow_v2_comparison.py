import pytest
from gov_mcp import frozen_qa as qa
from gov_mcp import frozen_qa_flow_v2 as flow


def unit(locator, text):
    return dict(source_id="X", locator=locator, text=text, text_sha256=qa.sha(text), revision_id="r1", original_sha256="original")


def answer(quote="换领工本费20元。", claim="换领工本费20元。", locator="p1"):
    return dict(outcome="answer", answer=claim, assertions=[dict(source_id="X", locator=locator, quote=quote, claim=claim)], stop_reason="", unknowns=[])


def stop():
    return dict(outcome="stop", answer="", assertions=[], stop_reason="未找到费用", unknowns=["费用"])


def adapter(outputs, calls):
    def chat(messages, contract, stage):
        calls.append(stage)
        return {"output": outputs.pop(0)}
    return chat


def test_stop_targeted_retrieval_accumulates(monkeypatch, tmp_path):
    a, b = unit("p1", "换领的办理要求。"), unit("p2", "换领工本费20元。")
    packets = iter([[a], [b]])
    monkeypatch.setattr(qa, "retrieve_with_plan", lambda *args: {"evidence": next(packets), "plan": {}})
    calls = []
    result = flow.run("换领要求和费用", [a,b], adapter([stop(), {"missing_items":["费用"], "next_query":"换领工本费"}, answer(locator="p2")], calls), tmp_path/"task", "corpus")
    assert result["decision"] == "CHECKED_CITATIONS_NUMBERS"
    assert calls == ["initial", "gap_plan", "repair"]
    assert [u["evidence_id"] for u in result["evidence"]] == ["E1", "E2"]
    assert result["retrieval_calls"] == 2


def test_quote_failure_one_repair(monkeypatch, tmp_path):
    u = unit("p1", "换领工本费20元。")
    monkeypatch.setattr(qa, "retrieve_with_plan", lambda *args: {"evidence":[u], "plan":{}})
    calls=[]
    result = flow.run("换领费用", [u], adapter([answer(quote="换领费用20元。"),answer()], calls), tmp_path/"task", "c")
    assert result["decision"] == "CHECKED_CITATIONS_NUMBERS"
    assert calls == ["initial", "repair"]
    assert result["retrieval_calls"] == 1


@pytest.mark.parametrize("question", ["今天办理费用", "江苏办理费用", "身份证号110101199001011234"])
def test_hard_gates_no_calls(question, tmp_path):
    calls=[]
    result=flow.run(question, [], adapter([], calls), tmp_path/"task", "c")
    assert result["decision"] == "STOP" and calls == []
    assert result["events"][0]["stop_origin"] == "backend_gate"


def test_failure_distinct_from_insufficiency(monkeypatch, tmp_path):
    monkeypatch.setattr(qa, "retrieve_with_plan", lambda *args: {"evidence":[], "plan":{}})
    def fail(*args): raise OSError("do not expose provider body")
    r=flow.run("换领费用", [], fail, tmp_path/"task", "c")
    assert r["decision"] == "EXECUTION_FAILED"
    assert "provider" not in (tmp_path/"task/state.json").read_text()


def test_unresolved_stop_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(qa, "retrieve_with_plan", lambda *args: {"evidence":[], "plan":{}})
    calls=[]
    r=flow.run("换领费用", [], adapter([stop(), {"missing_items":["费用"], "next_query":"费用"},stop()], calls), tmp_path/"task", "c")
    assert r["decision"] == "STOP" and len(calls)==3
    assert r["final"]["stop_reason"] == "UNRESOLVED_AFTER_REPAIR"


def test_task_version_text_and_restart_isolation(tmp_path):
    u=unit("p1", "换领20元。")
    task=flow.Task(tmp_path/"task", "换领费用", [u], "c")
    with pytest.raises(ValueError):task.add([u], "other", "task")
    with pytest.raises(ValueError):task.add([u], "c", "other")
    with pytest.raises(ValueError):task.add([{**u,"text":"换领10元。"}], "c", "task")
    task.add([u], "c", "task");task.add([u], "c", "task")
    assert len(task.state["evidence"])==1
    task.reserve_repair()
    with pytest.raises(ValueError): task.reserve_repair()
    with pytest.raises(FileExistsError):flow.Task(tmp_path/"task", "换领费用", [u], "c")


def test_exact_quote_and_unit_swap_rejected():
    u=unit("p1", "费用20元，期限60个工作日。")
    assert not flow.check(answer("费用20 元", "费用20元。"), [u])["passed"]
    assert not flow.check(answer(u["text"], "费用60元，期限20个工作日。"), [u])["passed"]


def test_final_claim_rendering_no_free_summary(monkeypatch, tmp_path):
    u=unit("p1", "换领工本费20元。")
    monkeypatch.setattr(qa, "retrieve_with_plan", lambda *args: {"evidence":[u],"plan":{}})
    out={**answer(),"answer":"换领工本费20元。另可代办。"}
    r=flow.run("换领费用", [u], adapter([out], []), tmp_path/"task", "c")
    assert r["final"]["answer"] == "换领工本费20元。"

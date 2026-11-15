"""MCP service of the bounded cross-document flow: tasks, budget and replay (synthetic data)."""
import json

import pytest

from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow
from gov_mcp.frozen_qa_flow_server import FlowService, create_app
from test_frozen_qa_flow_v3 import QUESTION, RENEW, TEMP, UNITS, WRONG, answer, scripted


def service(tmp_path, units=UNITS, **options):
    return FlowService(units, "c" * 64, tmp_path / "tasks", **options)


def test_open_gates_before_creating_a_task(tmp_path):
    svc = service(tmp_path)
    stop = svc.open("现在换领工本费是多少")
    assert stop["status"] == "STOP" and "task_id" not in stop and stop["final"]["outcome"] == "stop"
    assert list((tmp_path / "tasks").iterdir()) == []
    assert svc.open("")["error"] == "QUESTION_REQUIRED_MAX_500_CHARS"
    opened = svc.open(QUESTION)
    assert opened["status"] == "OK" and opened["max_drafts"] == flow.MAX_MODEL_CALLS
    assert opened["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert frozen_qa.PROPOSED_RULES in opened["answer_rules"]
    assert flow.LIGHT_RULES in service(tmp_path, rules="light").open(QUESTION)["answer_rules"]


def test_a_passing_draft_is_final_and_a_finished_task_only_replays(tmp_path):
    svc = service(tmp_path)
    task_id = svc.open(QUESTION)["task_id"]
    good = answer("到期换领工本费20元。", RENEW)
    first = svc.submit(task_id, good)
    assert (first["action"], first["result_kind"], first["drafts_used"]) == ("final", "answer", 1)
    assert first["final_checks"]["passed"] and first["replayed"] is False
    again = svc.submit(task_id, answer("到期换领工本费60元。", WRONG))
    assert again["replayed"] is True and again["final"] == first["final"] and again["drafts_used"] == 1


def test_failed_checks_ask_for_one_revision_then_salvage_or_stop(tmp_path):
    svc = service(tmp_path)
    task_id = svc.open(QUESTION)["task_id"]
    bad = answer("到期换领工本费60元。", WRONG)
    first = svc.submit(task_id, bad)
    assert (first["action"], first["reason"], first["drafts_left"]) == ("revise", "FAILED_CHECKS", 2)
    assert any("QUANTITY_NOT_QUOTED" in code for code in first["failures"])
    second = svc.submit(task_id, bad)
    assert second["action"] == "final" and second["final"]["stop_reason"] == "VERIFICATION_FAILED"
    assert second["stop_origin"] == "VERIFICATION_FAILED_AFTER_REPAIR" and second["drafts_used"] == 2

    task_id = svc.open("临时居民身份证有效期限和到期换领的工本费分别是什么？")["task_id"]
    mixed = answer("临时证有效期限为三个月；到期换领工本费60元。", TEMP, WRONG)
    assert svc.submit(task_id, mixed)["action"] == "revise"
    partial = svc.submit(task_id, mixed)
    assert (partial["result_kind"], partial["claims_excerpted"]) == ("partial_answer", 1)
    assert partial["final_checks"]["passed"]


def test_a_stop_gets_more_evidence_once(tmp_path):
    svc = service(tmp_path)
    task_id = svc.open(QUESTION)["task_id"]
    stop = answer("缺少依据。", outcome="stop", reason="证据不足", unknowns=["户口迁移准予迁入证明"])
    more = svc.submit(task_id, stop)
    assert (more["action"], more["reason"]) == ("revise", "MORE_EVIDENCE")
    assert [item["source_id"] for item in more["evidence_added"]] == ["DOC-OTHER"]
    final = svc.submit(task_id, answer("到期换领工本费20元。", RENEW))
    assert final["result_kind"] == "answer" and final["drafts_used"] == 2


def test_the_service_and_ask_reach_the_same_result(tmp_path):
    for drafts in ([answer("到期换领工本费20元。", RENEW)],
                   [answer("到期换领工本费60元。", WRONG), answer("到期换领工本费20元。", RENEW)],
                   [answer("到期换领工本费60元。", WRONG)] * 2):
        record = flow.ask(QUESTION, UNITS, scripted(*drafts))
        svc = service(tmp_path)
        task_id, result = svc.open(QUESTION)["task_id"], None
        for draft in drafts:
            result = svc.submit(task_id, draft)
        assert result["action"] == "final" and result["final"] == record["final"]
        assert (result["result_kind"], result["stop_origin"]) == (record["result_kind"], record["stop_origin"])


def test_budget_survives_restart_and_malformed_or_foreign_requests_are_refused(tmp_path):
    svc = service(tmp_path)
    task_id = svc.open(QUESTION)["task_id"]
    assert svc.submit(task_id, {"outcome": "answer"})["error"] == "ANSWER_CONTRACT_VIOLATION"
    assert svc.submit("../../etc/passwd", answer("x"))["error"] == "UNKNOWN_TASK"
    assert svc.submit("a" * 32, answer("x"))["error"] == "UNKNOWN_TASK"
    bad = answer("到期换领工本费60元。", WRONG)
    assert svc.submit(task_id, bad)["action"] == "revise"
    restarted = service(tmp_path)  # same task directory, new process state
    final = restarted.submit(task_id, bad)
    assert final["action"] == "final" and final["drafts_used"] == 2
    other = FlowService(UNITS, "d" * 64, tmp_path / "tasks")
    assert other.submit(task_id, bad)["error"] == "TASK_BELONGS_TO_ANOTHER_CORPUS_OR_PROFILE"
    # Drafts reserved by attempts that never finished cannot be extended.
    task_id = svc.open(QUESTION)["task_id"]
    path = tmp_path / "tasks" / (task_id + ".json")
    task = json.loads(path.read_text("utf-8"))
    task["drafts"] = [bad] * flow.MAX_MODEL_CALLS
    path.write_text(json.dumps(task, ensure_ascii=False), "utf-8")
    exhausted = svc.submit(task_id, answer("到期换领工本费20元。", RENEW))
    assert exhausted["stop_origin"] == "DRAFT_BUDGET_EXHAUSTED" and exhausted["final"]["outcome"] == "stop"


def test_app_needs_a_dedicated_token(tmp_path):
    with pytest.raises(ValueError, match="DEDICATED_TOKEN_REQUIRED"):
        create_app(service(tmp_path), "short")
    assert create_app(service(tmp_path), "t" * 40) is not None

"""Offline repair guidance checks; these do not measure model answer quality."""
import json

from gov_mcp import frozen_qa_flow_v3 as flow
from test_frozen_qa_flow_v3 import UNITS, WRONG, QUESTION, answer, scripted


def test_feedback_binds_failed_claim_and_source_version_without_new_evidence():
    output = answer("到期换领工本费60元", WRONG)
    pool = [dict(unit, revision_id="archived-v1", original_sha256="a" * 64) for unit in UNITS]
    item = flow.repair_feedback(output, ["A1_QUANTITY_NOT_QUOTED:60元"], pool)[0]
    assert item["assertion_index"] == 1 and item["unsupported_value"] == "60元"
    assert item["citation"] == {"source_id": WRONG[1], "locator": WRONG[2]}
    assert item["source_version"]["revision_id"] == "archived-v1"
    assert "20元" not in json.dumps(item, ensure_ascii=False)  # no invented replacement


def test_global_failures_stay_global_and_unknown_codes_are_retained():
    feedback = flow.repair_feedback(answer("错", WRONG), [
        "DATE_NOT_QUOTED:page_publication_date:2026-01-01", "FUTURE_CHECK", "FUTURE_CHECK"])
    assert len(feedback) == 2 and all(item["scope"] == "output" for item in feedback)
    assert all("assertion_index" not in item for item in feedback)
    assert feedback[1]["code"] == "FUTURE_CHECK"


def test_repair_does_not_relax_checks_or_add_calls():
    wrong = answer("到期换领工本费60元", WRONG)
    chat = scripted(wrong, wrong)
    record = flow.ask(QUESTION, UNITS, chat)
    assert record["result_kind"] == "stop" and len(chat.calls) == 2
    feedback_message = chat.calls[1][-1]["content"]
    assert "assertion_index" in feedback_message and "QUANTITY_NOT_QUOTED" in feedback_message
    assert record["status"]["business_conclusion_status"] == "PAUSED_UNREVIEWED"


def test_mcp_returns_the_same_structured_feedback(tmp_path):
    from test_frozen_qa_flow_server import service
    svc = service(tmp_path)
    task = svc.open(QUESTION)["task_id"]
    wrong = answer("到期换领工本费60元", WRONG)
    response = svc.submit(task, wrong)
    assert response["reason"] == "FAILED_CHECKS"
    assert response["repair_feedback"] == flow.repair_feedback(wrong, response["failures"], UNITS)
    assert response["drafts_used"] == 1 and response["drafts_left"] == 2


def test_mixed_versions_do_not_borrow_support_but_unrelated_claims_survive():
    from test_frozen_qa_flow_v3 import TEMP
    first = dict(UNITS[1], revision_id="old")
    second = dict(UNITS[1], revision_id="new", locator="正文段落2")
    pool = [first, second, UNITS[3]]
    mixed = answer("到期换领工本费20元；临时居民身份证有效期限为三个月。",
                   ("到期换领工本费20元", first["source_id"], first["locator"], "到期换领居民身份证的，工本费20元"), TEMP)
    checked = flow.check(mixed, pool)
    assert "A1_SOURCE_VERSION_AMBIGUOUS" in checked["failures"]
    kept, dropped, _ = flow.salvage(mixed, pool)
    assert kept is not None and flow.check(kept, pool)["passed"]
    assert all(c["source_id"] != first["source_id"] for c in kept["assertions"])


def test_same_identity_different_text_is_ambiguous_even_without_version_metadata():
    first = UNITS[1]
    second = dict(first, text=first["text"] + "另有说明。")
    draft = answer("到期换领工本费20元", ("到期换领工本费20元", first["source_id"], first["locator"], "到期换领居民身份证的，工本费20元"))
    assert "A1_SOURCE_VERSION_AMBIGUOUS" in flow.check(draft, [first, second])["failures"]


def test_reading_requirements_and_version_fields_match_cli_and_mcp(tmp_path):
    from gov_mcp.frozen_qa_flow_server import FlowService
    unit = dict(UNITS[1], revision_id="old", original_sha256="a"*64)
    svc=FlowService([unit], "c"*64, tmp_path/'tasks')
    opened=svc.open(QUESTION)
    assert opened['evidence'][0]['revision_id']=='old'
    assert opened['fact_requirements'][0]['semantic_coverage_verified'] is False
    assert flow.FACT_CHECKLIST_RULES in opened['answer_rules']


def test_persisted_tasks_require_same_implementation_and_followup_policy(tmp_path):
    from test_frozen_qa_flow_server import service
    svc=service(tmp_path)
    task=svc.open(QUESTION)['task_id']
    changed=service(tmp_path,follow_up_after_answer=True)
    output=answer("到期换领工本费20元",("到期换领工本费20元",UNITS[1]['source_id'],UNITS[1]['locator'],"到期换领居民身份证的，工本费20元"))
    assert changed.submit(task,output)['error']=='TASK_IMPLEMENTATION_CHANGED_OPEN_NEW_TASK'


def test_revision_alias_cannot_borrow_a_title_from_a_different_version():
    body=dict(UNITS[1],revision='old')
    title=dict(UNITS[0],revision='new')
    output=answer('2019年通知记载到期换领工本费20元',
                  ('2019年通知记载到期换领工本费20元',body['source_id'],body['locator'],'到期换领居民身份证的，工本费20元'))
    pool=[body]
    attached=flow.attach_source_titles(output,pool,[body,title])
    assert attached['assertions']==output['assertions'] and pool==[body]
    assert 'A1_SOURCE_VERSION_AMBIGUOUS' in flow.check(output,[body,title])['failures']

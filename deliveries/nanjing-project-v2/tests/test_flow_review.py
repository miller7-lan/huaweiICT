"""Evidence-driven flow simplification; integrity and publication gates remain active."""
import json

import pytest

from gov_mcp.decision.workflow import Workflow


FACTS = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}
BRANCH = "branch-adult-self"


@pytest.fixture
def workflow(tmp_path):
    return Workflow(tmp_path)


def test_first_round_contains_complete_dependency_evidence_and_staged_answers_match(workflow):
    answer = workflow.query(FACTS, BRANCH)
    task = workflow.get("query", answer["task_id"])
    needed = {span["id"] for row in task["draft"]["rows"] for span in row["evidence"]}
    assert set(task["rounds"][0]["requested"]) == needed
    assert set(task["rounds"][0]["found"]) == needed
    assert not task["rounds"][0]["evidence_gaps"]
    assert answer["retrieval_rounds"] == 1
    opened = workflow.open_query(FACTS, BRANCH)
    workflow.retrieve_query(opened["task_id"])
    assert not workflow.verify_query(opened["task_id"])["needs_more"]
    staged = workflow.finalize_query(opened["task_id"])
    for key in ("stages", "pending", "excluded", "paused", "counts", "follow_up", "snapshot_sha256"):
        assert staged[key] == answer[key]


def test_partial_retrieval_only_repeats_the_actual_gap(workflow):
    requests = []
    def retrieve(number, refs, snapshot):
        requests.append(list(refs))
        return refs[:1] if number == 1 else refs
    answer = workflow.query(FACTS, BRANCH, retriever=retrieve)
    assert answer["counts"]["confirmed"] == 4 and answer["retrieval_rounds"] == 2
    assert set(requests[1]) == set(requests[0]) - {requests[0][0]}


def test_staged_partial_retrieval_and_retry_survive_restart(workflow):
    key = workflow.open_query(FACTS, BRANCH)["task_id"]
    first = workflow.retrieve_query(key)
    with workflow.store.db(True) as db:
        task = workflow.get("query", key, db)
        task["retrieval"]["evidence"] = task["retrieval"]["evidence"][:1]
        workflow.put("query", key, task, db)
    result = workflow.verify_query(key)
    assert result["needs_more"] and workflow.verify_query(key) == result
    restored = Workflow(workflow.store.root)
    second = restored.retrieve_query(key)
    assert second["requested"] == result["evidence_gaps"]
    assert len(second["requested"]) == len(first["requested"]) - 1
    assert not restored.verify_query(key)["needs_more"]
    assert restored.finalize_query(key)["counts"]["confirmed"] == 4


def test_forged_span_is_not_counted_as_verified(workflow):
    key = workflow.open_query(FACTS, BRANCH)["task_id"]
    retrieved = workflow.retrieve_query(key)
    forged_id = retrieved["evidence"][0]["id"]
    with workflow.store.db(True) as db:
        task = workflow.get("query", key, db)
        task["retrieval"]["evidence"][0]["quote"] += "伪造文本"
        workflow.put("query", key, task, db)
    result = workflow.verify_query(key)
    assert forged_id in result["evidence_gaps"] and forged_id not in result["found"]
    assert workflow.retrieve_query(key)["requested"] == [forged_id]
    workflow.verify_query(key)
    assert workflow.finalize_query(key)["counts"]["confirmed"] == 4


def test_unknown_user_facts_do_not_trigger_an_extra_evidence_round(workflow):
    facts = {key: value for key, value in FACTS.items() if key != "electronic_verified"}
    answer = workflow.query(facts, BRANCH)
    assert answer["retrieval_rounds"] == 1
    assert answer["follow_up"]["enabled"]
    assert answer["counts"]["confirmed"] == 3
    assert workflow.history(answer["task_id"])["rounds"][0]["unknown_user_fields"] == ["electronic_verified"]


def test_no_requirements_skip_retrieval_but_still_finalize_with_current_state(workflow):
    facts = {**FACTS, "age_group": "minor"}
    def forbidden(*args):
        pytest.fail("No evidence is needed for an empty requirement set")
    answer = workflow.query(facts, "branch-minor-self", retriever=forbidden)
    assert answer["retrieval_rounds"] == 0 and answer["status"] == "no_confirmed_requirements"
    opened = workflow.open_query(facts, "branch-minor-self")
    assert opened["status"] == "can_finalize"
    assert workflow.verify_query(opened["task_id"])["round"] == 0
    assert workflow.finalize_query(opened["task_id"])["retrieval_rounds"] == 0
    assert workflow.verify_query(opened["task_id"])["round"] == 0


@pytest.mark.parametrize("minor", [False, True])
def test_native_action_template_follows_persisted_state_without_empty_child_calls(workflow, monkeypatch, minor):
    from gov_mcp.decision.workflow import ROOT
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from provision_agent_workflow import QUERY_ACTION
    exported = json.loads((ROOT / "config/agents.workflow-query_controller.nexent-v2.7.0.json").read_text("utf-8"))
    controller = next(agent for agent in exported["agent_info"].values() if agent["is_main_agent"])
    assert controller["few_shots_prompt"] == QUERY_ACTION
    assert controller["duty_prompt"].endswith(QUERY_ACTION)
    facts = {**FACTS, "age_group": "minor"} if minor else FACTS
    request = {"facts": facts, "branch_id": "branch-minor-self" if minor else BRANCH}
    calls, answers = [], []
    def retrieve(task):
        calls.append("retriever")
        return workflow.retrieve_query(json.loads(task)["task_id"])
    def verify(task):
        calls.append("verifier")
        return workflow.verify_query(json.loads(task)["task_id"])
    action = QUERY_ACTION.removeprefix("<code>\n").removesuffix("\n</code>")
    action = action.replace("REQUEST_JSON_LITERAL", repr(json.dumps(request)))
    exec(action, {"govkb_open_query": workflow.open_query, "govflow_retriever": retrieve,
        "govflow_query_verifier": verify, "govkb_query_state": lambda task_id: workflow.get("query", task_id),
        "govkb_finalize_query": workflow.finalize_query, "final_answer": answers.append})
    assert calls == ([] if minor else ["retriever", "verifier"])
    assert json.loads(answers[0])["retrieval_rounds"] == (0 if minor else 1)

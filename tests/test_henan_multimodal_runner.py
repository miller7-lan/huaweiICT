"""Henan runner acceptance checks on synthetic sources and fabricated events.

The temporary workspace executes its real source-bound plan; SSE events are
fabricated contract fixtures and never evidence of a model or native run.
"""
from copy import deepcopy
import json

import pytest

import run_henan_multimodal_native as runner
from provision_henan_multimodal_native import NAMES
from test_henan_multimodal_preview import documents, integrated, stages  # noqa: F401
from test_ontology_crossdoc_runner import synthetic_events


@pytest.fixture
def completed(integrated):
    preview, _, _, _, _ = integrated
    run, task = stages(preview, verify=True)
    answer = preview.finalize(task)
    saved = {"agents": {"controller": 101, "retriever": 102, "verifier": 103},
             "draft_bodies": {role: {"name": NAMES[role]} for role in NAMES}}
    events = synthetic_events(saved)
    events[-1]["content"] = json.dumps(answer, ensure_ascii=False)
    return preview, run, saved, events, answer


def replace_answer(events, value):
    changed = deepcopy(events)
    changed[-1]["content"] = json.dumps(value, ensure_ascii=False)
    return changed


def test_counterfactual_four_node_plan_with_seventeen_quotes_is_accepted(completed):
    preview, run, saved, events, answer = completed
    result = runner.assess(events, preview, run, saved)
    assert result["passed"] and all(result["checks"].values())
    assert result["answer"] == answer
    proof = answer["reasoning"]
    # These deliberately false policy values come from the synthetic source
    # fixture, preventing a historical 20-yuan / 30-day answer from being baked
    # into the runner instead of checking the reviewed source-derived proof.
    assert proof["archive_result"]["ordinary_renewal_fee_yuan"] == 23
    assert proof["archive_result"]["historical_duration"]["quoted_days"] == 29
    assert len(proof["trace"]) == 4
    citations = {(item["source_id"], item["locator"], item["text_sha256"])
                 for node in proof["trace"] for item in node["evidence"]}
    assert len(citations) == 17
    assert result["checks"]["all_seventeen_quotes_bound"]
    assert result["checks"]["four_typed_dependent_nodes"]


@pytest.mark.parametrize("path,replacement", [
    (["formal_approval"], 0),
    (["reasoning", "formal_approval"], 0),
    (["reasoning", "trace", 1, "output"], 23.0),
    (["reasoning", "archive_result", "ordinary_renewal_fee_yuan"], 23.0),
])
def test_equal_value_different_json_type_is_rejected(completed, path, replacement):
    preview, run, saved, events, answer = completed
    changed = deepcopy(answer)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    result = runner.assess(replace_answer(events, changed), preview, run, saved)
    assert result["passed"] is False and result["answer"] is None
    assert result["checks"]["persisted_answer"] is False


@pytest.mark.parametrize("mutation", ["missing", "quote_hash"])
def test_missing_or_changed_located_quote_is_rejected(completed, mutation):
    preview, run, saved, events, answer = completed
    changed = deepcopy(answer)
    trace = changed["reasoning"]["trace"]
    selected = trace[0]["evidence"][0]
    if mutation == "missing":
        for node in trace:
            node["evidence"] = [item for item in node["evidence"]
                                if (item["source_id"], item["locator"]) !=
                                (selected["source_id"], selected["locator"])]
    else:
        selected["quote"] += " synthetic changed text"
    result = runner.assess(replace_answer(events, changed), preview, run, saved)
    assert result["passed"] is False and result["answer"] is None
    assert result["checks"]["all_seventeen_quotes_bound"] is False
    assert result["checks"]["persisted_answer"] is False


def test_unreviewed_dependency_change_is_rejected(completed):
    preview, run, saved, events, answer = completed
    changed = deepcopy(answer)
    changed["reasoning"]["trace"][1]["depends_on"] = []
    result = runner.assess(replace_answer(events, changed), preview, run, saved)
    assert result["passed"] is False
    assert result["checks"]["four_typed_dependent_nodes"] is False
    assert result["checks"]["reviewed_henan_plan_executed"] is False


def test_extra_tool_is_rejected_even_when_final_answer_is_exact(completed):
    preview, run, saved, events, _ = completed
    changed = deepcopy(events)
    changed.insert(-1, {"type": "tool", "tool_name": "unapproved_publish", "depth": 0})
    result = runner.assess(changed, preview, run, saved)
    assert result["passed"] is False and result["checks"]["no_extra_tools"] is False
    assert result["checks"]["persisted_answer"] is True


def test_verification_tool_must_belong_to_the_exact_child_invocation(completed):
    preview, run, saved, events, _ = completed
    changed = deepcopy(events)
    next(event for event in changed if event.get("tool_name") == "govkb_ontology_crossdoc_verify")[
        "invocation_id"] = "wrong-child"
    result = runner.assess(changed, preview, run, saved)
    assert result["passed"] is False and result["checks"]["child_invocation_bindings"] is False


def test_observed_model_starts_over_campaign_limit_are_rejected(completed):
    preview, run, saved, events, _ = completed
    changed = [{"type": "model_attempt_control", "phase": "begin", "attempt": 1}
               for _ in range(runner.MAX_MODEL_STARTS)] + deepcopy(events)
    result = runner.assess(changed, preview, run, saved)
    assert result["observed_model_starts"] == runner.MAX_MODEL_STARTS + 1
    assert result["passed"] is False and result["checks"]["observed_model_budget"] is False

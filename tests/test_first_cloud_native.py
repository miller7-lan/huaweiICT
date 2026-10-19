"""Native acceptance must reject plausible prose and require persisted tool results."""
from copy import deepcopy
import json

from verify_first_cloud_native import assess


ANSWER = {"task_id": "task-synthetic", "status": "partial", "counts": {"confirmed": 3, "pending": 1, "paused": 1}}


class Persisted:
    def get(self, kind, key):
        assert kind == "query" and key == ANSWER["task_id"]
        return {"status": "complete", "answer": deepcopy(ANSWER)}


def trace():
    events = [{"type": "tool", "tool_name": n, "depth": 0} for n in
              ("read_skill_md", "govkb_open_query", "govkb_query_state", "govkb_finalize_query")]
    events += [{"type": "subagent_start"}, {"type": "tool", "tool_name": "govkb_retrieve_query", "depth": 1},
               {"type": "subagent_start"}, {"type": "tool", "tool_name": "govkb_verify_query", "depth": 1},
               {"type": "final_answer", "content": json.dumps(ANSWER)}]
    return events


def test_partial_answer_is_valid_when_actual_tools_and_persistence_agree():
    result = assess(trace(), Persisted())
    assert result["passed"] and result["answer"] == ANSWER


def test_forged_final_without_execution_cannot_pass():
    result = assess([{"type": "final_answer", "content": json.dumps(ANSWER)}], Persisted())
    assert not result["passed"] and "ACTUAL_TOOL_TRACE_MISSING" in result["stop_reasons"]


def test_both_verification_and_retrieval_must_run_in_children():
    events = trace()
    next(e for e in events if e.get("tool_name") == "govkb_verify_query")["depth"] = 0
    assert "ACTUAL_CHILD_TRACE_MISSING" in assess(events, Persisted())["stop_reasons"]


def test_rewritten_answer_cannot_override_paused_or_pending_counts():
    events = trace()
    rewritten = deepcopy(ANSWER)
    rewritten["counts"]["paused"] = 0
    events[-1]["content"] = json.dumps(rewritten)
    assert "PERSISTED_RESULT_MISMATCH" in assess(events, Persisted())["stop_reasons"]


def test_unexecuted_markup_or_runtime_failure_cannot_pass():
    events = trace()
    events[-1]["content"] = '<invoke name="govkb_finalize_query" />'
    assert not assess(events, Persisted())["passed"]
    events = trace() + [{"type": "max_steps_reached"}]
    assert "NATIVE_RUNTIME_ERROR" in assess(events, Persisted())["stop_reasons"]

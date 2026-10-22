"""Runner lifecycle contracts with fabricated events and temporary files only.

These checks never contact Nexent, a provider, or the project's private runtime.
Fake traces test the runner's acceptance and cleanup logic; they are not proof
of a real model run, human business review, or source/policy correctness.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import provision_ontology_crossdoc_native as provision
import verify_ontology_crossdoc_native as runner


BASELINE, REVIEWED, RUN_ID = "a" * 64, "b" * 64, "c" * 32
TASK_ID = "d" * 32


def synthetic_answer():
    context = {"version": REVIEWED, "binding": "e" * 64}
    trace = [{"id": name, "depends_on": parents, "output": value,
              "ontology_context_binding": context["binding"],
              "evidence": [{"quote": "Synthetic contract fixture only"}]}
             for name, parents, value in (("c1", [], 25), ("u1", ["c1"], 22),
                                          ("j1", ["c1", "u1"], "false"))]
    return {"task_id": TASK_ID, "published": False, "formal_approval": False,
            "ontology_context": context,
            "reasoning": {"schema_version": 1, "formal_approval": False,
                          "reason": "REVIEWED_ONTOLOGY_PLAN_EXECUTED",
                          "ontology_context": deepcopy(context), "trace": trace,
                          "business_conclusion_status": "PAUSED_UNREVIEWED",
                          "current_applicability": "unknown"}}


def synthetic_events(saved):
    result = [{"type": "model_attempt_control", "phase": "begin", "attempt": 1}]
    for stage in runner.STAGES:
        if stage in {"govkb_ontology_crossdoc_retrieve", "govkb_ontology_crossdoc_verify"}:
            role = "retriever" if stage.endswith("retrieve") else "verifier"
            result.extend([
                {"type": "subagent_start", "agent_id": saved["agents"][role], "depth": 1,
                 "invocation_id": "synthetic-" + role},
                {"type": "tool", "tool_name": saved["draft_bodies"][role]["name"], "depth": 0},
            ])
        child = stage.endswith(("retrieve", "verify"))
        result.append({"type": "tool", "tool_name": stage, "depth": 1 if child else 0,
                       **({"agent_id": saved["agents"][role], "invocation_id": "synthetic-" + role} if child else {})})
        if child:
            result.append({"type": "subagent_end", "agent_id": saved["agents"][role],
                           "depth": 1, "invocation_id": "synthetic-" + role})
    result.append({"type": "final_answer", "depth": 0, "content": json.dumps(synthetic_answer())})
    return result


class FakeWorkspace:
    def __init__(self):
        self.head = BASELINE
        self.operations = []
        self.fail_cleanup = False

    def status(self):
        return {"head": self.head}

    def rollback(self, target, expected, actor, *, confirmed=False):
        assert self.head == expected and actor["kind"] == "human" and confirmed is True
        self.operations.append((expected, target))
        if target == BASELINE and self.fail_cleanup:
            raise OSError("Synthetic cleanup failure")
        self.head = target


class FakePreview:
    def __init__(self):
        self.workspace = FakeWorkspace()
        self.expected_version = REVIEWED
        self.reserved = 0
        self.run_status = None
        self.end_error = False

    def counts(self):
        return {"native_runs": self.reserved, "max_native_runs": 1,
                "run_status": self.run_status, "tasks": int(bool(self.reserved))}

    def preflight(self):
        assert self.workspace.head == REVIEWED
        return {"ontology_context": synthetic_answer()["ontology_context"]}

    def begin_run(self):
        if self.reserved:
            raise ValueError("NATIVE_CAMPAIGN_BUDGET_EXHAUSTED")
        self.reserved = 1
        self.run_status = "active"
        return RUN_ID

    def end_run(self, run_id, passed, failure=None):
        assert run_id == RUN_ID and self.run_status == "active"
        if self.end_error:
            raise OSError("Synthetic terminal-state persistence failure")
        self.run_status = "passed" if passed else "blocked"

    def get_task(self, task_id):
        assert task_id == TASK_ID
        return {"run_id": RUN_ID, "status": "complete", "answer": synthetic_answer()}

    def finalize(self, task_id):
        assert task_id == TASK_ID and self.workspace.head == REVIEWED
        return synthetic_answer()


class FakeResponse:
    def __init__(self, api):
        self.api = api

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.api.transport_failure:
            raise httpx.ConnectError("Synthetic disconnected transport")

    def iter_lines(self):
        for event in self.api.events:
            self.api.consumed_events += 1
            yield "data: " + json.dumps(event)


class FakeAPI:
    token = "SYNTHETIC_TEST_TOKEN"
    runtime_base = "http://synthetic.invalid"

    def __init__(self, preview, events):
        self.http = self
        self.preview, self.events = preview, events
        self.stream_requests = 0
        self.consumed_events = 0
        self.stops = 0
        self.transport_failure = False

    def stream(self, method, url, **kwargs):
        assert method == "POST" and url.endswith("/agent/run")
        assert self.preview.reserved == 1 and self.preview.workspace.head == REVIEWED
        self.stream_requests += 1
        return FakeResponse(self)

    def runtime_call(self, method, path, body=None):
        if path == "/conversation/create":
            assert method == "PUT" and self.preview.reserved == 1
            return {"data": {"conversation_id": "SYNTHETIC_CONVERSATION"}}
        assert method == "GET" and path == "/agent/stop/SYNTHETIC_CONVERSATION"
        self.stops += 1
        return {}


@pytest.fixture
def campaign(monkeypatch, tmp_path):
    runtime = tmp_path / "private-synthetic-runtime"
    output = tmp_path / "synthetic-evidence"
    runtime.mkdir()
    binding = {"baseline": BASELINE, "expected_version": REVIEWED}
    (runtime / "ontology-binding.json").write_text(json.dumps(binding), "utf-8")
    preview = FakePreview()
    saved = {"agents": {"controller": 101, "retriever": 102, "verifier": 103},
             "model_id": 104, "other_signatures": {"other": "synthetic"},
             "draft_bodies": {role: {"name": "synthetic_" + role}
                              for role in ("retriever", "verifier")}}
    api = FakeAPI(preview, synthetic_events(saved))
    monkeypatch.setattr(runner, "RUNTIME", runtime)
    monkeypatch.setattr(runner, "OUTPUT", output)
    monkeypatch.setattr(runner, "open_preview", lambda *_: preview)
    monkeypatch.setattr(runner, "source_pins", lambda: {"implementation": "synthetic"})
    monkeypatch.setattr(runner, "protected_pins", lambda: {"old": "synthetic"})
    monkeypatch.setattr(provision, "other_signatures", lambda *_: saved["other_signatures"])
    for name in ("check_service", "check_drafts", "check_skill"):
        monkeypatch.setattr(provision, name, lambda *_: None)
    # No production NexentAPI instance or socket is ever constructed.
    monkeypatch.setattr(runner, "NexentAPI", lambda: pytest.fail("Real API forbidden in runner tests"))
    return preview, api, saved, runtime, output


def test_transport_failure_spends_one_budget_stops_and_rolls_back(campaign):
    preview, api, saved, _, output = campaign
    api.transport_failure = True
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] is False and report["rolled_back"] is True
    assert report["error_code"] == "TRANSPORT_UNAVAILABLE"
    assert preview.reserved == 1 and preview.run_status == "blocked"
    assert api.stream_requests == api.stops == 1
    assert preview.workspace.operations == [(BASELINE, REVIEWED), (REVIEWED, BASELINE)]
    assert json.loads((output / "latest.json").read_text("utf-8"))["passed"] is False
    with pytest.raises(ValueError, match="NATIVE_CAMPAIGN_BUDGET_EXHAUSTED"):
        runner.live(api, saved, "Synthetic test reviewer")
    assert api.stream_requests == 1


def test_live_evidence_does_not_inherit_zero_call_claims_from_plan(campaign):
    preview, api, saved, _, _ = campaign
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] and report["rolled_back"]
    assert report["native_run_requests"] == 1 and report["observed_model_starts"] == 1
    # Provider totals may remain unknown; a real live request cannot be labelled zero.
    assert report.get("model_calls") != 0
    assert report.get("http_calls") != 0
    assert preview.run_status == "passed" and api.stops == 0


def test_terminal_state_write_failure_still_persists_failed_report_and_rolls_back(campaign):
    preview, api, saved, _, output = campaign
    preview.end_error = True
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] is False and report["rolled_back"] is True
    assert preview.workspace.head == BASELINE and preview.reserved == 1
    latest = json.loads((output / "latest.json").read_text("utf-8"))
    evidence = json.loads((output / latest["evidence"]).read_text("utf-8"))
    assert evidence["status"] == "FAILED" and latest["passed"] is False


def test_rollback_failure_remains_failed_without_extra_native_request(campaign):
    preview, api, saved, _, output = campaign
    preview.workspace.fail_cleanup = True
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] is False and report["rolled_back"] is False
    assert report["rollback_failed"] is True
    assert api.stream_requests == 1 and preview.reserved == 1
    assert json.loads((output / "latest.json").read_text("utf-8"))["passed"] is False


@pytest.mark.parametrize("failure_event", [
    {"type": "execution_logs", "content": '{"status":"blocked","error_code":"SOURCE_PAUSED"}'},
    {"type": "warning", "content": "Code execution failed: Synthetic fixture"},
    {"type": "final_answer", "depth": 0, "content": "DSML synthetic unexecuted call"},
    {"type": "error", "content": "Synthetic model error"},
    {"type": "max_steps_reached", "depth": 1},
])
def test_tool_or_execution_failure_stops_before_later_model_event(campaign, failure_event):
    preview, api, saved, _, _ = campaign
    api.events = [failure_event, {"type": "model_attempt_control", "phase": "begin", "attempt": 1}]
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] is False and report["rolled_back"] is True
    assert api.stream_requests == api.stops == 1
    assert api.consumed_events == 1
    assert preview.reserved == 1 and preview.run_status == "blocked"


def test_observed_model_budget_stops_stream_without_request_retry(campaign):
    preview, api, saved, _, _ = campaign
    api.events = [{"type": "model_attempt_control", "phase": "begin", "attempt": 1}
                  for _ in range(runner.MAX_OBSERVED_MODEL_STARTS + 2)]
    report = runner.live(api, saved, "Synthetic test reviewer")
    assert report["passed"] is False and report["rolled_back"] is True
    assert report["error_code"] == "OBSERVED_MODEL_START_STOP_THRESHOLD_EXCEEDED"
    assert api.consumed_events == runner.MAX_OBSERVED_MODEL_STARTS + 1
    assert api.stream_requests == api.stops == preview.reserved == 1


def test_default_cli_only_writes_plan_without_api(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "OUTPUT", tmp_path / "synthetic-plan")
    monkeypatch.setattr(sys, "argv", ["verify_ontology_crossdoc_native.py"])
    monkeypatch.setattr(runner, "NexentAPI", lambda: pytest.fail("Plan must not instantiate API"))
    monkeypatch.setattr(runner, "open_preview", lambda *_: pytest.fail("Plan must not open private runtime"))
    assert runner.main() == 0
    result = json.loads((runner.OUTPUT / "native-run-plan.json").read_text("utf-8"))
    assert result["status"] == "PREPARED_NOT_EXECUTED"
    assert result["model_calls"] == result["http_calls"] == 0
    assert result["native_run_budget"] == 1 and result["whole_run_retries"] == 0


def test_live_cli_requires_current_explicit_confirmation_before_api(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify_ontology_crossdoc_native.py", "--live"])
    monkeypatch.setattr(runner, "NexentAPI", lambda: pytest.fail("Unconfirmed run must not instantiate API"))
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


def test_native_child_metadata_is_sufficient_without_wrapper_tool_event(campaign):
    preview, api, saved, _, _ = campaign
    wrappers = {saved["draft_bodies"][r]["name"] for r in ("retriever", "verifier")}
    api.events = [e for e in api.events if not (e.get("type") == "tool" and e.get("tool_name") in wrappers)]
    assert runner.live(api, saved, "Synthetic test reviewer")["passed"] is True


def test_child_invocation_must_bind_exact_tool_between_start_and_end(campaign):
    _, api, saved, _, _ = campaign
    events = deepcopy(api.events)
    event = next(e for e in events if e.get("tool_name") == "govkb_ontology_crossdoc_verify")
    event["invocation_id"] = "wrong-child"
    assert runner.child_invocations(events, saved) is False


@pytest.mark.parametrize("path,replacement", [
    (["reasoning", "trace", 0, "output"], 25.0),
    (["reasoning", "schema_version"], True),
    (["reasoning", "formal_approval"], 0),
])
def test_root_final_type_changes_fail_persistence_and_close_campaign(campaign, path, replacement):
    preview, api, saved, _, _ = campaign
    answer = json.loads(api.events[-1]["content"])
    target = answer
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    api.events[-1]["content"] = json.dumps(answer)
    result = runner.live(api, saved, "Synthetic test reviewer")
    assert result["passed"] is False
    assert result["checks"]["persisted_answer"] is False
    assert result["rolled_back"] is True and preview.run_status == "blocked"
    assert api.stream_requests == api.stops == preview.reserved == 1


def test_deeply_nested_final_is_failed_closed_without_uncaught_recursion(campaign):
    preview, api, saved, _, _ = campaign
    depth = sys.getrecursionlimit() + 100
    api.events[-1]["content"] = '{"nested":' + '[' * depth + '0' + ']' * depth + '}'
    result = runner.live(api, saved, "Synthetic test reviewer")
    assert result["passed"] is False and result["rolled_back"] is True
    assert result["checks"]["structured_persisted_answer"] is False
    assert preview.run_status == "blocked" and api.stream_requests == api.stops == 1

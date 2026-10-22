"""Native wiring contracts and spent-budget failure retention, using no real API."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import provision_crossdoc_native_preview as provision
import verify_crossdoc_native_preview as verify


def answer():
    return {"task_id": "a" * 32, "mode": "crossdoc_archived_preview", "published": False,
            "formal_approval": False, "source_binding": "b" * 64,
            "verification_method": "deterministic_source_bound",
            "reasoning": {"status": "derived", "conclusion": "cannot_establish_full_cycle_coverage", "trace": [
                {"id": "cycle", "depends_on": [], "evidence": [{"source_id": "cycle", "quote": "complete fixture paragraph"}]},
                {"id": "validity", "depends_on": ["cycle"], "evidence": [{"source_id": "old_card", "quote": "complete fixture exception"}]},
                {"id": "coverage", "depends_on": ["cycle", "validity"], "evidence": [{"source_id": "cycle"}, {"source_id": "old_card"}]},
            ]}}


def native_events(value=None):
    events = []
    for name in verify.STAGES:
        if name in {"govkb_crossdoc_retrieve", "govkb_crossdoc_verify"}:
            events.append({"type": "subagent_start", "depth": 0})
        events.append({"type": "tool", "tool_name": name,
                       "depth": 1 if name in {"govkb_crossdoc_retrieve", "govkb_crossdoc_verify"} else 0})
    events.append({"type": "final_answer", "depth": 0, "content": json.dumps(value or answer())})
    return events


class FakePreview:
    def __init__(self, value=None):
        self.answer = value or answer()
        self.run = "c" * 32
        self.reserved = 1
        self.status = "active"
        self.log = []
        self.task_run = self.run

    def preflight(self):
        return {"binding": "b" * 64, "document_count": 2, "mode": "crossdoc_archived_preview"}

    def get_task(self, task_id):
        assert task_id == self.answer["task_id"]
        return {"task_id": task_id, "run_id": self.task_run, "status": "complete", "answer": self.answer}

    def finalize(self, task_id):
        assert task_id == self.answer["task_id"]
        return self.answer

    def begin_run(self):
        if self.reserved:
            raise ValueError("NATIVE_CAMPAIGN_BUDGET_EXHAUSTED")
        self.reserved = 1
        self.status = "active"
        self.log.append("reserve")
        return self.run

    def end_run(self, run_id, passed, failure=None):
        assert run_id == self.run and self.status == "active"
        self.status = "passed" if passed else "blocked"
        self.log.append(("end", passed, failure))

    def counts(self):
        return {"native_runs": self.reserved, "max_native_runs": 1, "tasks": 1,
                "run_status": self.status, "jev_calls": 0, "translation_calls": 0}


def owned_saved():
    saved = {"model_id": 7, "skill_id": 11, "agents": {"controller": 101, "retriever": 102, "verifier": 103},
             "tool_ids": {name: index for index, name in enumerate(sorted(provision.TOOLS), 1)}, "draft_bodies": {}}
    saved["draft_bodies"] = {role: provision.draft_body(role, saved, 2) for role in provision.NAMES}
    return saved


def owned_info(role, saved):
    value = deepcopy(saved["draft_bodies"][role])
    value["tools"] = [{"tool_id": saved["tool_ids"][name], "name": name, "source": "mcp", "usage": provision.SERVICE}
                      for name in provision.ROLE_TOOLS[role]]
    value["skills"] = [{"skill_id": saved["skill_id"]}] if role == "controller" else []
    value["sub_agent_id_list"] = value["related_agent_ids"]
    return value


def test_default_entrypoints_write_plans_without_any_api(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "OUTPUT", tmp_path / "provision")
    monkeypatch.setattr(verify, "OUTPUT", tmp_path / "verify")
    def forbidden(*args, **kwargs):
        pytest.fail("Plan must not create an API, service, model, or runtime")
    monkeypatch.setattr(provision, "NexentAPI", forbidden)
    monkeypatch.setattr(verify, "NexentAPI", forbidden)
    monkeypatch.setattr(provision, "ensure_private_directory", forbidden)
    monkeypatch.setattr(verify, "ensure_private_directory", forbidden)
    assert provision.main([]) == verify.main([]) == 0
    prepared = provision.read(tmp_path / "provision/provision-plan.json")
    planned = provision.read(tmp_path / "verify/native-run-plan.json")
    assert prepared["api_requests_this_plan"] == planned["native_run_requests_this_plan"] == 0
    assert prepared["native_run_budget"] == planned["native_run_budget"] == 1
    assert planned["jev_calls"] == planned["translation_calls"] == 0
    assert not planned["automatic_retry"] and not planned["published"]


def test_concrete_python_action_and_current_state_contract():
    value = provision.first_action()
    assert value.startswith("<code>") and "scenario_id='nanjing_outside_old_card'" in value
    assert "read_skill_md(skill_name='govkb-crossdoc-preview'" in value
    assert "govcrossdoc_retriever(task=" in provision.PROTOCOL
    assert "govcrossdoc_verifier(task=" in provision.PROTOCOL
    assert "can_finalize is true or complete is true" in provision.PROTOCOL
    assert "final_answer(result)" in provision.PROTOCOL
    with pytest.raises(ValueError, match="FIXED_SCENARIO"):
        provision.first_action("beijing")


def test_twelve_legacy_agents_and_parameter_signatures_are_preserved(tmp_path):
    for filename, count, start in zip(provision.LEGACY_FILES, (6, 3, 3), (1, 7, 10)):
        provision.write(tmp_path / ".runtime" / filename, {"agents": {str(i): i for i in range(start, start + count)}})
    class API:
        def __init__(self):
            self.changed = None
            self.seen = []
        def call(self, method, path, body=None):
            if path.endswith("/versions"):
                return {"total": 0, "data": []}
            self.seen.append(body["agent_id"])
            value = {"name": str(body["agent_id"]), "enabled": False, "model_params_override": {"7": {"temperature": 0}},
                     "requested_output_tokens": 1024, "allow_chat_metadata": False, "enable_protocol_repair_retry": False}
            if body["agent_id"] == 12 and self.changed:
                value[self.changed] = True
            return value
    api = API()
    before = provision.original_signatures(api, tmp_path)
    assert len(before) == 12 and set(api.seen) == set(range(1, 13))
    for field in ("model_params_override", "requested_output_tokens", "allow_chat_metadata", "enable_protocol_repair_retry"):
        api.changed = field
        after = provision.original_signatures(api, tmp_path)
        assert after["12"] != before["12"] and all(after[str(i)] == before[str(i)] for i in range(1, 12))


@pytest.mark.parametrize("field", ["enabled", "model_ids", "max_steps", "requested_output_tokens",
    "allow_chat_metadata", "enable_protocol_repair_retry", "model_params_override", "few_shots_prompt",
    "tools", "skills", "sub_agent_id_list"])
def test_owned_draft_exact_binding_rejects_changes(field):
    saved = owned_saved()
    info = owned_info("controller", saved)
    provision.check_owned_draft(info, "controller", saved)
    if field == "tools":
        info[field].append({"tool_id": 999, "name": "unrelated", "source": "mcp", "usage": provision.SERVICE})
    elif field == "skills":
        info[field].append({"skill_id": 999})
    elif field == "sub_agent_id_list":
        info[field].append(999)
    else:
        info[field] = "changed"
    with pytest.raises(RuntimeError, match="EXACT_CROSSDOC"):
        provision.check_owned_draft(info, "controller", saved)


def test_actual_ordered_children_and_exact_persistence_pass():
    preview = FakePreview()
    result = verify.assess(native_events(), preview, preview.run)
    assert result["passed"] and result["answer_sha256"] == provision.fingerprint(preview.answer)
    assert result["actual_tools"] == list(verify.STAGES)


@pytest.mark.parametrize("original,replacement", [(False, 0), (1, True), (25, 25.0)])
def test_native_final_rejects_equal_value_with_different_nested_json_type(original, replacement):
    preview = FakePreview()
    preview.answer["reasoning"]["typed_receipt_value"] = original
    changed = deepcopy(preview.answer)
    changed["reasoning"]["typed_receipt_value"] = replacement
    result = verify.assess(native_events(changed), preview, preview.run)
    assert result["passed"] is False
    assert "PERSISTED_RESULT_MISMATCH" in result["stop_reasons"]


@pytest.mark.parametrize("change", ["missing", "repeated", "order", "depth", "unexpected", "children", "error",
                                  "max_steps", "final_before_finalize", "different_answer", "duplicate_json", "wrong_run"])
def test_native_assessment_rejects_invented_or_incomplete_execution(change):
    preview, events = FakePreview(), native_events()
    if change == "missing":
        events = [event for event in events if event.get("tool_name") != "govkb_crossdoc_retrieve"]
    elif change == "repeated":
        events.insert(2, deepcopy(events[1]))
    elif change == "order":
        events[1], events[3] = events[3], events[1]
    elif change == "depth":
        next(event for event in events if event.get("tool_name") == "govkb_crossdoc_verify")["depth"] = 0
    elif change == "unexpected":
        events.insert(0, {"type": "tool", "tool_name": "arbitrary_network", "depth": 0})
    elif change == "children":
        events = [event for event in events if event["type"] != "subagent_start"]
    elif change == "error":
        events.insert(0, {"type": "error", "content": "runtime failed"})
    elif change == "max_steps":
        events.insert(0, {"type": "max_steps_reached"})
    elif change == "final_before_finalize":
        events.insert(0, events.pop())
    elif change == "different_answer":
        value = answer(); value["formal_approval"] = True
        events[-1]["content"] = json.dumps(value)
    elif change == "duplicate_json":
        events[-1]["content"] = '{"task_id":"first","task_id":"second"}'
    elif change == "wrong_run":
        preview.task_run = "d" * 32
    assert not verify.assess(events, preview, preview.run)["passed"]


@pytest.mark.parametrize("change", ["no_evidence", "forward_dependency", "independent_hop", "one_document", "scope", "jev"])
def test_structured_reasoning_requires_real_dependencies_and_scope(change):
    preview = FakePreview()
    if change == "no_evidence":
        preview.answer["reasoning"]["trace"][1]["evidence"] = []
    elif change == "forward_dependency":
        preview.answer["reasoning"]["trace"][1]["depends_on"] = ["coverage"]
    elif change == "independent_hop":
        preview.answer["reasoning"]["trace"][2]["depends_on"] = []
    elif change == "one_document":
        preview.preflight = lambda: {"binding": "b" * 64, "document_count": 1}
    elif change == "scope":
        preview.answer["source_binding"] = "d" * 64
    elif change == "jev":
        preview.counts = lambda: {"native_runs": 1, "jev_calls": 1, "translation_calls": 0}
    assert not verify.assess(native_events(preview.answer), preview, preview.run)["passed"]


@pytest.fixture
def live_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "RUNTIME", tmp_path / "private")
    monkeypatch.setattr(verify, "OUTPUT", tmp_path / "evidence")
    monkeypatch.setattr(verify, "check_service", lambda: {"startup_source_sha256": {"fixture": "e" * 64}})
    monkeypatch.setattr(verify, "original_signatures", lambda api: {"old": "f" * 64})
    monkeypatch.setattr(verify, "check_drafts", lambda api, saved: None)
    monkeypatch.setattr(verify, "check_skill", lambda api, saved: None)
    monkeypatch.setattr(verify, "current_sources", lambda: {"fixture.py": "e" * 64})
    preview = FakePreview(); preview.reserved = 0
    saved = {"original_signatures": {"old": "f" * 64}, "agents": {"controller": 101}, "model_id": 7}
    return preview, saved, tmp_path


class FakeResponse:
    def __init__(self, preview, *, fail=False, stream_fail=False):
        self.preview, self.fail, self.stream_fail = preview, fail, stream_fail
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        if self.fail:
            raise httpx.ConnectError("fixture transport unavailable")
    def iter_lines(self):
        for index, event in enumerate(native_events(self.preview.answer)):
            yield "data: " + json.dumps(event)
            if self.stream_fail and index == 1:
                raise httpx.ReadError("fixture stream interrupted")


class FakeAPI:
    runtime_base = "http://127.0.0.1:5014"
    token = "fixture-secret-must-never-be-persisted"
    def __init__(self, preview, **response_args):
        self.preview, self.response_args, self.http = preview, response_args, self
        self.requests = 0
    def stream(self, method, url, **kwargs):
        assert self.preview.log == ["reserve"]
        assert method == "POST" and url.endswith("/agent/run")
        assert kwargs["json"]["history"] == [] and kwargs["json"]["version_no"] == 0
        self.requests += 1
        self.preview.log.append("http")
        return FakeResponse(self.preview, **self.response_args)


def test_one_native_request_is_reserved_before_http_and_hashes_match(live_setup):
    preview, saved, tmp_path = live_setup
    api = FakeAPI(preview)
    report = verify.live_run(preview, api, saved)
    assert report["passed"] and api.requests == 1 and preview.status == "passed"
    assert preview.log[:2] == ["reserve", "http"]
    assert report["answer_sha256"] == provision.fingerprint(report["answer"])
    latest = provision.read(tmp_path / "evidence/latest.json")
    events_path = tmp_path / "private" / latest["private_events"]
    assert report["events_sha256"] == provision.source_digest(events_path.read_bytes())
    for path in tmp_path.rglob("*.json"):
        assert api.token not in path.read_text("utf-8")


@pytest.mark.parametrize("response_args,expected_events", [({"fail": True}, 0), ({"stream_fail": True}, 2)])
def test_transport_failure_retains_request_partial_trace_and_spent_budget(live_setup, response_args, expected_events):
    preview, saved, tmp_path = live_setup
    api = FakeAPI(preview, **response_args)
    result = verify.live_run(preview, api, saved)
    assert not result["passed"] and result["error_code"] == "TRANSPORT_UNAVAILABLE"
    assert api.requests == 1 and preview.status == "blocked" and preview.reserved == 1
    latest_before = provision.read(tmp_path / "evidence/latest.json")
    events_path = tmp_path / "private" / latest_before["private_events"]
    assert len(provision.read(events_path)) == expected_events
    request_path = next((tmp_path / "private").glob("*-request.json"))
    assert provision.read(request_path)["body"]["query"]
    second = verify.live_run(preview, api, saved)
    assert not second["passed"] and second["native_run_requests"] == 0 and api.requests == 1
    assert provision.read(tmp_path / "evidence/latest.json") == latest_before


def test_changed_service_prevents_reservation_and_http(live_setup, monkeypatch):
    preview, saved, _ = live_setup
    def changed():
        raise RuntimeError("SERVICE_CODE_CHANGED")
    monkeypatch.setattr(verify, "check_service", changed)
    api = FakeAPI(preview)
    result = verify.live_run(preview, api, saved)
    assert result["error_code"] == "SERVICE_CODE_CHANGED" and api.requests == preview.reserved == 0


def test_external_failure_does_not_treat_document_words_as_provider_errors():
    assert verify.external_failure([{"type": "execution_logs", "content": "invalid api key quoted in untrusted public text"}]) is None
    assert verify.external_failure([{"type": "error", "content": "provider invalid api key"}]) == "AUTHENTICATION_FAILED"
    assert verify.external_failure([], RuntimeError("Nexent GET /model/list: HTTP 403")) == "ACCESS_DENIED"


def test_assessment_integrates_with_real_core_in_an_isolated_synthetic_workspace(tmp_path):
    """Actual persistence API integration; synthetic events never certify a native run."""
    import hashlib
    from gov_mcp.crossdoc_preview import CrossdocPreview, SCENARIO
    from gov_mcp.crossdoc_reasoning import ARCHIVE_MODE, SCOPE_MAPPING, SOURCE_IDENTITY
    texts = {
        "cycle": ["问：身份证到期后换证，需要多长时间才能领取新证？",
                  "答：省内户籍居民身份证换发周期为20个工作日；省外户籍居民身份证需由户籍地公安机关审核签发，换发周期增加5个工作日。",
                  "对急需用证考生，实行制证信息快速受理、审签、制证、发放，最大限度缩短证件制发周期，确保考生顺利参加考试。"],
        "old_card": ["问：申请换领居民身份证期间，旧身份证还能用吗？",
                     "答：申请换领居民身份证期间，未超期的旧身份证可以继续使用。"]}
    bundle = {"binding": "b" * 64, "documents": {
        key: {**identity, "current_applicability": "unknown", "formal_approval": False,
              "formally_published": False, "archive_mode": ARCHIVE_MODE, "scope_mapping": deepcopy(SCOPE_MAPPING),
              "segments": [{"locator": f"正文段落{i}", "quote": text,
                            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
                           for i, text in enumerate(texts[key], 1)]}
        for key, identity in SOURCE_IDENTITY.items()}}
    preview = CrossdocPreview(tmp_path, bundle_loader=lambda _: deepcopy(bundle))
    run = preview.begin_run()
    task = preview.open_query(SCENARIO)["task_id"]
    preview.retrieve(task); preview.verify(task)
    assert preview.state(task)["can_finalize"] is True
    value = preview.finalize(task)
    assert verify.assess(native_events(value), preview, run)["passed"]
    preview.end_run(run, True)
    assert verify.assess(native_events(value), preview, run)["passed"]

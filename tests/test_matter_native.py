"""Native-stage boundary fixtures. No LLM, Jev or human acceptance is simulated as real."""
from datetime import datetime
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gov_mcp.matter_lifecycle import MatterLifecycle, Principal, LifecycleError
from gov_mcp.matter_native import MatterNative, NativeMatterError, SCENARIO, SERVICE, BATCHED, native_profile
from gov_mcp.matter_native_server import create_app

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
BASELINE = json.loads((PACK / "rules-and-sources.json").read_text(encoding="utf-8"))
NOW = max(datetime.fromisoformat(s["checked_at"]).timestamp() for s in BASELINE["sources"]) + 10
MAINT = Principal("fixture-maintainer", "maintainer", "automated_test")


def release(service, stage):
    candidate = service.prepare(PACK, ontology_stage=stage, principal=MAINT)
    return service.release_preview(candidate["candidate_id"], service.head(),
        note="Automated stage fixture, not human review", principal=MAINT)["version"]


@pytest.fixture
def native(tmp_path):
    tick = [NOW]
    service = MatterLifecycle(tmp_path / "service", clock=lambda: tick[0])
    release(service, "evolved")
    root = tmp_path / "native"; root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"schema": 1, "purpose": SERVICE,
        "native_run_limit": 2, "scenario_id": SCENARIO, "formal_publication": False}), encoding="utf-8")
    return MatterNative(root, service, clock=lambda: tick[0]), service, tick


def opened(native):
    app, service, tick = native
    run = app.begin()
    return app, service, tick, run, app.open(SCENARIO)["task_id"]


def test_real_source_stage_chain_persistence_and_exact_final(native):
    app, service, tick, run, task = opened(native)
    fetched = app.retrieve(task)
    assert len({s["source_id"] for s in fetched["segments"]}) >= 2
    assert app.retrieve(task) == fetched
    verified = app.verify(task)
    assert not verified["verification"]["jev_called"]
    restarted = MatterNative(app.root, service, clock=lambda: tick[0])
    assert restarted.state(task)["can_finalize"]
    result = restarted.finalize(task)
    assert result["proofs"] and result["human_review"] == "pending"
    assert not result["knowledge_published"] and not result["formal_business_answer"]
    assert restarted.finalize(task) == result == app.task(task)["answer"]
    app.finish(run, passed=True)
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): app.begin()
    with pytest.raises(NativeMatterError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"): app.state(task)


def test_no_activation_or_private_scenario_arguments(native):
    app, _, _ = native
    with pytest.raises(NativeMatterError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"): app.open(SCENARIO)
    app.begin()
    with pytest.raises(NativeMatterError, match="FIXED_PUBLIC_SCENARIO_REQUIRED"): app.open("other-case")
    app.open(SCENARIO)
    with pytest.raises(NativeMatterError, match="ONE_TASK_PER_RUN"): app.open(SCENARIO)


def test_stage_order_and_unknown_ids(native):
    app, _, _, _, task = opened(native)
    with pytest.raises(NativeMatterError, match="RETRIEVAL_REQUIRED"): app.verify(task)
    with pytest.raises(NativeMatterError, match="VERIFICATION_REQUIRED"): app.finalize(task)
    with pytest.raises(NativeMatterError, match="INVALID_TASK_ID"): app.retrieve("bad")
    with pytest.raises(NativeMatterError, match="TASK_NOT_FOUND"): app.retrieve("task-" + "0" * 32)


def test_retrieval_tampering_is_rejected(native):
    app, _, _, _, task_id = opened(native)
    app.retrieve(task_id)
    task = app.task(task_id); task["retrieval"]["segments"][0]["text"] = "tampered"
    app.store.put("runs", task_id, task)
    with pytest.raises(NativeMatterError, match="RETRIEVAL_INTEGRITY_FAILURE"): app.verify(task_id)


def test_version_pinned_while_engineering_head_changes(native):
    app, service, _, _, task = opened(native)
    pinned = app.task(task)["version"]
    seed = release(service, "seed")
    assert seed != pinned and service.head() == seed
    app.retrieve(task); app.verify(task)
    assert app.finalize(task)["version"] == pinned


@pytest.mark.parametrize("stage", ["opened", "retrieved", "verified", "complete"])
def test_current_pause_blocks_every_stage_including_completed_history(native, stage):
    app, service, _, _, task = opened(native)
    if stage != "opened": app.retrieve(task)
    if stage in ("verified", "complete"): app.verify(task)
    if stage == "complete": app.finalize(task)
    service.set_source_enabled("NJ-POLICE-FAQ-2025", False, reason="Fixture pause", principal=MAINT)
    for operation in (app.state, app.retrieve, app.verify, app.finalize):
        with pytest.raises(LifecycleError, match="SOURCE_PAUSED"): operation(task)


def test_failed_attempt_retry_budget_and_foreign_task(native):
    app, _, _, run, task = opened(native)
    app.finish(run, passed=False)
    with pytest.raises(NativeMatterError, match="CONCRETE_REPAIR_NOTE_REQUIRED"): app.begin()
    second = app.begin("Fixed observed protocol failure in the owned controller draft")
    with pytest.raises(NativeMatterError, match="TASK_OUTSIDE_ACTIVE_RUN"): app.state(task)
    app.finish(second, passed=False)
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): app.begin("another repair note")


def test_timed_out_run_not_restarted_or_budget_cleared(native):
    app, _, tick, run, task = opened(native)
    tick[0] += 601
    with pytest.raises(NativeMatterError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"): app.state(task)
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): app.begin()
    assert app.store.get("state", "campaign")["active"] == run


def test_external_failure_closes_campaign(native):
    app, _, _ = native
    run = app.begin(); app.finish(run, passed=False, external_failure=True)
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): app.begin("Concrete repair note")


def test_mcp_rejects_missing_token_and_untrusted_host(native):
    app = create_app(native[0], "fixture-token-" + "x" * 32)
    with TestClient(app, base_url="http://127.0.0.1:8785") as client:
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer fixture-token-" + "x" * 32,
            "Host": "untrusted.example"}).status_code == 421
        assert client.get("/health").json()["knowledge_published"] is False


def test_native_assessment_rejects_program_success_without_real_child_trace(native):
    import sys
    sys.path.insert(0, str(PACK.parents[3] / "scripts"))
    from verify_matter_native import assess
    app, _, _, run, task = opened(native)
    app.retrieve(task); app.verify(task); answer = app.finalize(task)
    # Explicit trace fixtures are only used to test false-success rejection.
    events = [{"type": "tool", "tool_name": name, "depth": 0} for name in
        ("read_skill_md", "govmatter_open", "govmatter_retrieve", "govmatter_verify", "govmatter_state", "govmatter_finalize")]
    events.append({"type": "final_answer", "content": answer, "depth": 0})
    result = assess(events, app, run)
    assert not result["passed"] and "ACTUAL_CHILD_TRACE_REQUIRED" in result["stop_reasons"]
    events[-1]["content"] = {**answer, "knowledge_published": True}
    assert "PERSISTENT_FINAL_RESULT_MISMATCH" in assess(events, app, run)["stop_reasons"]


def batched(native):
    legacy, service, tick = native
    root = legacy.root.parent / "batched-native"; root.mkdir()
    (root / "manifest.json").write_bytes((json.dumps(BATCHED) + "\n").encode())
    return MatterNative(root, service, clock=lambda: tick[0]), service, tick


def test_batched_requires_verified_state_ack_before_finalize(native):
    app, _, _ = batched(native)
    app.begin(); task = app.open(SCENARIO)["task_id"]
    with pytest.raises(NativeMatterError, match="VERIFICATION_REQUIRED_BEFORE_STATE_ACK"): app.state(task)
    app.retrieve(task); app.verify(task)
    with pytest.raises(NativeMatterError, match="VERIFIED_STATE_ACK_REQUIRED"): app.finalize(task)
    assert app.state(task)["can_finalize"]
    answer = app.finalize(task)
    assert answer == app.task(task)["answer"] and not answer["formal_business_answer"]


def test_batched_closing_action_executes_children_state_and_exact_final_in_one_action(native):
    import sys
    sys.path.insert(0, str(PACK.parents[3] / "scripts"))
    from provision_matter_native import closing_action
    app, _, _ = batched(native)
    app.begin(); task = app.open(SCENARIO)["task_id"]
    calls, final = [], []
    def child(operation, name):
        def call(task):
            calls.append(name); return operation(json.loads(task)["task_id"])
        return call
    def state(task_id): calls.append("state"); return app.state(task_id)
    def finish(task_id): calls.append("finalize"); return app.finalize(task_id)
    code = closing_action(native_profile("batched-close-v2"), task).removeprefix("<code>\n").removesuffix("\n</code>")
    env = {"govmatterbatch_retriever": child(app.retrieve, "retrieve"), "govmatterbatch_verifier": child(app.verify, "verify"),
        "govmatter_state": state, "govmatter_finalize": finish, "final_answer": final.append, "print": lambda *args: None}
    exec(compile(code, "explicit_fixed_protocol_fixture", "exec"), {"__builtins__": {}}, env)
    assert calls == ["retrieve", "verify", "state", "finalize"] and final == [app.task(task)["answer"]]


def test_new_batched_activity_never_resets_closed_legacy_budget(native):
    legacy, _, _ = native
    run = legacy.begin(); legacy.finish(run, passed=False)
    run = legacy.begin("Observed child protocol repair fixture"); legacy.finish(run, passed=False)
    app, _, _ = batched(native)
    run = app.begin(); app.finish(run, passed=False)
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): app.begin()
    with pytest.raises(NativeMatterError, match="CAMPAIGN_NOT_AVAILABLE"): legacy.begin("Another repair fixture note")
    assert len(legacy.store.get("state", "campaign")["attempts"]) == 2
    assert len(app.store.get("state", "campaign")["attempts"]) == 1


def test_batched_manifest_cannot_increase_budget(native):
    app, service, tick = batched(native)
    (app.root / "manifest.json").write_bytes(json.dumps({**BATCHED, "native_run_limit": 2}).encode())
    with pytest.raises(NativeMatterError, match="CAMPAIGN_MANIFEST_MISMATCH"): MatterNative(app.root, service)


def test_batched_pause_after_state_ack_still_stops_final(native):
    app, service, _ = batched(native)
    app.begin(); task = app.open(SCENARIO)["task_id"]
    app.retrieve(task); app.verify(task); app.state(task)
    service.set_source_enabled("NJ-POLICE-FAQ-2025", False, reason="Pause after acknowledged state fixture", principal=MAINT)
    with pytest.raises(LifecycleError, match="SOURCE_PAUSED"): app.finalize(task)


def test_batched_explicit_time_window_preserves_freshness_and_terminal_budget(native):
    app, _, tick = batched(native)
    run = app.begin(); task = app.open(SCENARIO)["task_id"]
    app.retrieve(task); app.verify(task)
    tick[0] += 601
    assert app.state(task)["can_finalize"]
    tick[0] += 600
    with pytest.raises(NativeMatterError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"): app.finalize(task)
    assert app.store.get("state", "campaign")["active"] == run

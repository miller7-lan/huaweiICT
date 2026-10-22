"""Controlled offline contracts; fixtures never prove official business or model success."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import importlib.util
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from gov_mcp.classification import ClassificationError, fingerprint
from gov_mcp.jev_translation import VERSION
from gov_mcp.public_preview import CASE, PAUSES, PORTAL, QUESTION, SOURCES, Preview, PreviewError, Sections, read, source_digest, write
from gov_mcp.public_preview_server import create_app


@pytest.fixture
def runtime(tmp_path):
    (tmp_path / "sources").mkdir()
    sources = []
    text = {"section-text_01": "测试户籍窗口", "section-text_02": "有效期满可换领。未满16周岁须监护人陪同。",
        "section-text_03": "15个工作日", "section-text_04": "20元/证。按测试通知规定。",
        "section-text_05": "测试窗口地址", "section-text_06": "0371—86191017", "section-text_07": "测试时间",
        "section-txt": "到户政窗口申领。", "section-txt1": "1、居民户口簿；2、原居民身份证；3、未满十六周岁由监护人陪同；4、跨户籍所在地市依条件交验。"}
    for identifier, url in SOURCES.items():
        raw = ("<html>" + "".join(f'<section id="{key}" title="测试字段">{value}</section>' for key, value in text.items()) + "</html>").encode()
        (tmp_path / "sources" / (identifier + ".html")).write_bytes(raw)
        parser = Sections(); parser.feed(raw.decode())
        sources.append({"source_id": identifier, "url": url, "data_class": "public", "raw_sha256": source_digest(raw),
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "acquisition_method": "official_http_archive",
            "published_at": None, "effective_at": None, "sections": parser.sections})
    portal_raw = b"<html>Controlled frontend shell only</html>"
    (tmp_path / "sources/portal.html").write_bytes(portal_raw)
    write(tmp_path / "manifest.json", {"schema": 1, "mode": "public_preview", "data_class": "public", "case": CASE,
        "sources": sources, "portal": {"url": PORTAL, "status": "http_shell", "checked_at": datetime.now(timezone.utc).isoformat(),
            "http_status": 200, "raw_sha256": source_digest(portal_raw), "method": "normal_public_http_no_login"}})
    write(tmp_path / "gates.json", {"mode": "public_preview", "paused_fields": PAUSES, "source_stopped": False})
    return tmp_path


class FakeVerifier:
    def __init__(self):
        self.calls = 0
        self.low = None
        self.error = None

    def __call__(self, state, questions, output):
        self.calls += 1
        if self.error:
            raise ClassificationError(self.error)
        response = {"model": "jev-1.13.0", "answers": {key: {"type": "noul", "noul": .03 if key == "invented_advance" else (.7 if key == self.low else .95)} for key in questions},
            "usage": {"input_tokens": 1, "output_tokens": 1}, "english_input": {"version": VERSION, "state": {"text": "controlled fixture"}, "questions": questions}}
        write(output, {"test_fixture": True, "result": response, "jev_receipts": [], "translation_receipts": []})
        return response


def opened(runtime, verifier=None):
    backend = verifier or FakeVerifier()
    preview = Preview(runtime, backend)
    run = preview.begin_run()
    task = preview.open_query(deepcopy(CASE), QUESTION)["task_id"]
    return preview, task, run, backend


@pytest.mark.parametrize("change", [{"data_class": "private"}, {"age_group": "minor"}, {"self_service": False}, {"self_service": 1}, {"extra": "visitor text"}])
def test_fixed_public_fictional_scope_cannot_expand(runtime, change):
    preview = Preview(runtime)
    case = {**CASE, **change}
    with pytest.raises(PreviewError, match="PUBLIC_FIXED_SCOPE_REQUIRED"):
        preview.open_query(case, QUESTION)


def test_no_native_run_no_cloud_permission(runtime):
    with pytest.raises(PreviewError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"):
        Preview(runtime).open_query(CASE, QUESTION)


def test_public_manifest_rejects_private_nested_source(runtime):
    manifest = read(runtime / "manifest.json")
    manifest["sources"][0]["data_class"] = "private"
    write(runtime / "manifest.json", manifest)
    with pytest.raises(PreviewError, match="UNAPPROVED_SOURCE_METADATA"):
        Preview(runtime).begin_run()


def test_source_parse_forgery_rejected(runtime):
    manifest = read(runtime / "manifest.json")
    manifest["sources"][0]["sections"]["section-text_04"]["text"] = "免费"
    write(runtime / "manifest.json", manifest)
    with pytest.raises(PreviewError, match="SOURCE_PARSE_CHANGED"):
        Preview(runtime).begin_run()


def test_claimed_portal_capture_needs_original_bytes(runtime):
    manifest = read(runtime / "manifest.json")
    manifest["portal"]["raw_sha256"] = None
    write(runtime / "manifest.json", manifest)
    with pytest.raises(PreviewError, match="PORTAL_PROVENANCE_INVALID"):
        Preview(runtime).begin_run()


def test_tampered_original_stops_before_model(runtime):
    preview, task, _, verifier = opened(runtime)
    preview.retrieve(task)
    (runtime / "sources/erqi.html").write_text("changed", "utf-8")
    with pytest.raises(PreviewError, match="SOURCE_HASH_CHANGED"):
        preview.verify(task)
    assert verifier.calls == 0


def test_stale_source_is_not_current(runtime):
    manifest = read(runtime / "manifest.json")
    manifest["sources"][0]["retrieved_at"] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    write(runtime / "manifest.json", manifest)
    with pytest.raises(PreviewError, match="SOURCE_REFRESH_REQUIRED"):
        Preview(runtime).begin_run()


def test_forged_stored_quote_does_not_reach_model(runtime):
    preview, task, _, verifier = opened(runtime)
    preview.retrieve(task)
    with preview.db() as db:
        value = preview._get(db, task)
        value["facts"][0]["citation"]["quote"] = "invented"
        preview._save(db, value)
    with pytest.raises(PreviewError, match="EXACT_RETRIEVAL_REQUIRED"):
        preview.verify(task)
    assert verifier.calls == 0


def test_same_task_reuses_verification_but_not_across_tasks(runtime):
    preview, task, _, verifier = opened(runtime)
    preview.retrieve(task)
    preview.verify(task)
    assert preview.verify(task)["cached_for_same_task"] is True
    answer = preview.finalize(task)
    assert answer == preview.finalize(task)
    assert verifier.calls == 1 and preview.counts()["jev_reserved"] == 1


def test_low_support_and_business_conflicts_are_independent(runtime):
    fake = FakeVerifier(); fake.low = "materials"
    preview, task, _, _ = opened(runtime, fake)
    preview.retrieve(task); preview.verify(task)
    answer = preview.finalize(task)
    assert next(c for c in answer["source_facts"] if c["id"] == "materials")["status"] == "needs_review"
    assert next(c for c in answer["source_facts"] if c["id"] == "fee")["status"] == "source_supported"
    assert {p["field"] for p in answer["pending_business_conclusions"]} == set(PAUSES)
    assert all(p["status"] == "paused" for p in answer["pending_business_conclusions"])
    assert answer["unknown_fields"] == ["advance_days"] and answer["invented_advance_rejected"]
    assert answer["published"] is answer["formal_approval"] is False


@pytest.mark.parametrize("after_complete", [False, True])
def test_changed_current_pause_invalidates_even_completed_answer(runtime, after_complete):
    preview, task, _, _ = opened(runtime)
    preview.retrieve(task); preview.verify(task)
    if after_complete:
        preview.finalize(task)
    gates = read(runtime / "gates.json"); gates["paused_fields"]["materials"] += "新增待核。"
    write(runtime / "gates.json", gates)
    with pytest.raises(PreviewError, match="SNAPSHOT_OR_PAUSE_CHANGED"):
        preview.finalize(task)


def test_receipt_tampering_invalidates_answer(runtime):
    preview, task, _, _ = opened(runtime)
    preview.retrieve(task); preview.verify(task)
    write(runtime / (task + "-jev.json"), {"result": "rewritten"})
    with pytest.raises(PreviewError, match="MODEL_RECEIPT_CHANGED"):
        preview.finalize(task)


def test_external_failure_cannot_repeat_network(runtime):
    fake = FakeVerifier(); fake.error = "ACCESS_RESTRICTED"
    preview, task, run, _ = opened(runtime, fake)
    preview.retrieve(task)
    with pytest.raises(PreviewError, match="ACCESS_RESTRICTED"):
        preview.verify(task)
    with pytest.raises(PreviewError, match="LIVE_VERIFICATION_NOT_READY"):
        preview.verify(task)
    preview.finish_run(run, False)
    with pytest.raises(PreviewError, match="CAMPAIGN_STOPPED"):
        preview.begin_run("do not retry an external failure")
    assert fake.calls == 1


def test_native_budget_requires_repairs_and_stops_at_three(runtime):
    preview = Preview(runtime)
    for number in range(3):
        run = preview.begin_run("specific implemented repair" if number else None)
        if not number:
            with pytest.raises(PreviewError, match="DOCUMENTED_REPAIR_REQUIRED"):
                preview.begin_run()
        preview.finish_run(run, False)
    with pytest.raises(PreviewError, match="NATIVE_RUN_BUDGET_EXHAUSTED"):
        preview.begin_run("another implemented repair")


def test_passed_campaign_cannot_refresh_timestamp_with_more_calls(runtime):
    preview = Preview(runtime); run = preview.begin_run(); preview.finish_run(run, True)
    with pytest.raises(PreviewError, match="CAMPAIGN_STOPPED"):
        preview.begin_run("not needed after success")


def test_jev_budget_is_durable(runtime):
    preview, task, _, fake = opened(runtime); preview.retrieve(task)
    with preview.db() as db:
        db.execute("INSERT INTO counters VALUES('jev_reserved',3)")
    with pytest.raises(PreviewError, match="JEV_CAMPAIGN_STOPPED"):
        preview.verify(task)
    assert fake.calls == 0


def test_old_task_cannot_spend_another_runs_call_budget(runtime):
    preview, task, run, fake = opened(runtime); preview.retrieve(task)
    preview.finish_run(run, False)
    preview.begin_run("specific corrected native protocol")
    with pytest.raises(PreviewError, match="TASK_NOT_IN_ACTIVE_RUN"):
        preview.verify(task)
    assert fake.calls == 0


def test_token_and_origin_boundaries(runtime):
    app = create_app(Preview(runtime), "x"*40)
    with TestClient(app, base_url="http://localhost:8781") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer " + "x"*40, "Origin": "https://evil.invalid"}).status_code in (403, 421)


def test_native_assessment_rejects_prose_instead_of_tools(runtime):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import verify_public_native_preview as runner
    result = runner.assess([{"type": "final_answer", "content": "done"}], Preview(runtime), "wrong")
    assert not result["passed"] and "ACTUAL_TOOL_TRACE_MISSING" in result["stop_reasons"]


def test_native_assessment_rejects_root_only_child_operations(runtime):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import verify_public_native_preview as runner
    events = [{"type": "tool", "tool_name": n, "depth": 0} for n in ["read_skill_md", "govkb_preview_open_query", "govkb_preview_retrieve", "govkb_preview_verify", "govkb_preview_state", "govkb_preview_finalize"]]
    result = runner.assess(events, Preview(runtime), "wrong")
    assert not result["passed"] and "ACTUAL_CHILD_TRACE_MISSING" in result["stop_reasons"]


def test_one_translation_has_no_implicit_second_attempt(monkeypatch):
    from gov_mcp.public_preview import OneTranslation
    from gov_mcp.jev_translation import DeepSeekTranslator
    def controlled(self, texts):
        self.attempts += 1
        return {"output": texts}
    monkeypatch.setattr(DeepSeekTranslator, "translate", controlled)
    translator = OneTranslation(enabled=True)
    translator.translate({"one": "controlled"})
    with pytest.raises(ClassificationError, match="TRANSLATION_BUDGET_EXHAUSTED"):
        translator.translate({"two": "controlled"})


def test_http_quota_cause_survives_single_attempt_budget(runtime, monkeypatch):
    import gov_mcp.public_preview as module
    class QuotaProvider:
        name = "jev"
        def __init__(self, key, max_calls):
            assert max_calls == 1
            self.api_key = key
            self.receipts = []
        def check_ready(self, model):
            pass
        def evaluate(self, state, questions, model):
            self.receipts.append({"http_status": 429, "status": "error"})
            raise ClassificationError("TEST_BUDGET_EXHAUSTED")
    monkeypatch.setenv("TYPESAFE_API_KEY", "controlled-fixture")
    monkeypatch.setattr(module, "load_key", lambda profile: "controlled-fixture")
    monkeypatch.setattr(module, "JevProvider", QuotaProvider)
    with pytest.raises(PreviewError, match="RATE_LIMITED"):
        module.LiveVerifier()({"data_class": "public", "text": "controlled"},
            {"q": {"type": "noul", "instructions": "controlled"}}, runtime / "quota.json")
    assert read(runtime / "quota.json")["jev_receipts"][0]["http_status"] == 429


def test_preview_tool_names_must_bind_to_exact_service():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from provision_public_native_preview import check_owned_draft, ROLE_TOOLS
    saved = {"model_id": 4, "tool_ids": {"govkb_preview_retrieve": 1}, "skill_id": 5, "agents": {}}
    info = {"name": "govpreview_retriever", "enabled": False, "model_ids": [4],
        "tools": [{"tool_id": 1, "name": "govkb_preview_retrieve", "source": "mcp", "usage": "govkb_public_preview"}],
        "skills": [], "sub_agent_id_list": [], "max_steps": 3, "requested_output_tokens": 1024,
        "enable_protocol_repair_retry": False, "allow_chat_metadata": False}
    check_owned_draft(info, "retriever", saved)
    info["tools"][0]["usage"] = "unrelated_mcp"
    with pytest.raises(RuntimeError, match="EXACT_PREVIEW_DRAFT_BINDING_REQUIRED"):
        check_owned_draft(info, "retriever", saved)

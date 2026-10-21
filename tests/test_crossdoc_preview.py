"""Synthetic service/budget tests; no native model or official source claims."""
from copy import deepcopy
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from gov_mcp.crossdoc_preview import CrossdocPreview, PreviewError, SCENARIO
from gov_mcp.crossdoc_preview_server import create_app
from gov_mcp.crossdoc_reasoning import ARCHIVE_MODE, SCOPE_MAPPING, SOURCE_IDENTITY


@pytest.fixture
def preview(tmp_path):
    texts = {
        "cycle": ["问：身份证到期后换证，需要多长时间才能领取新证？",
                  "答：省内户籍居民身份证换发周期为20个工作日；省外户籍居民身份证需由户籍地公安机关审核签发，换发周期增加5个工作日。",
                  "对急需用证考生，实行制证信息快速受理、审签、制证、发放，最大限度缩短证件制发周期，确保考生顺利参加考试。"],
        "old_card": ["问：申请换领居民身份证期间，旧身份证还能用吗？",
                     "答：申请换领居民身份证期间，未超期的旧身份证可以继续使用。"]}
    bundle = {"binding": "a" * 64, "documents": {
        key: {**identity, "current_applicability": "unknown", "formal_approval": False, "formally_published": False,
              "archive_mode": ARCHIVE_MODE, "scope_mapping": deepcopy(SCOPE_MAPPING),
              "segments": [{"locator": f"正文段落{i}", "quote": text,
                            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
                           for i, text in enumerate(texts[key], 1)]}
        for key, identity in SOURCE_IDENTITY.items()}}
    return CrossdocPreview(tmp_path, bundle_loader=lambda _: deepcopy(bundle)), bundle


def opened(preview):
    run = preview.begin_run()
    task = preview.open_query(SCENARIO)["task_id"]
    return run, task


def complete(preview):
    run, task = opened(preview)
    preview.retrieve(task)
    preview.verify(task)
    preview.state(task)
    return run, task, preview.finalize(task)


def test_campaign_reserves_one_before_tools_and_failure_cannot_reopen(preview):
    p, _ = preview
    with pytest.raises(PreviewError): p.open_query(SCENARIO)
    run = p.begin_run()
    assert p.counts()["native_runs"] == 1
    p.end_run(run, False, "FIXTURE_FAILURE")
    with pytest.raises(PreviewError): p.begin_run()
    with pytest.raises(PreviewError): p.open_query(SCENARIO)
    assert p.counts()["native_runs"] == 1


def test_real_stages_store_exact_final_and_allow_post_success_revalidation(preview):
    p, _ = preview
    run, task, answer = complete(p)
    assert answer["reasoning"]["status"] == "derived"
    assert answer["reasoning"]["usual_waiting_working_days"] == 25
    assert p.get_task(task)["answer"] == answer
    p.end_run(run, True)
    assert p.finalize(task) == answer
    assert p.counts()["run_status"] == "passed"
    assert answer["published"] is False and answer["formal_approval"] is False


@pytest.mark.parametrize("operation", ["verify", "finalize"])
def test_stage_skipping_does_not_compute_an_answer(preview, operation):
    p, _ = preview
    _, task = opened(p)
    with pytest.raises(PreviewError): getattr(p, operation)(task)
    assert p.get_task(task)["answer"] is None


@pytest.mark.parametrize("stage", ["retrieve", "verify", "finalize"])
def test_source_pause_or_changed_binding_rejects_pinned_task(preview, stage):
    p, bundle = preview
    _, task = opened(p)
    if stage in ("verify", "finalize"): p.retrieve(task)
    if stage == "finalize": p.verify(task)
    bundle["binding"] = "b" * 64
    with pytest.raises(PreviewError, match="SOURCE_OR_IMPLEMENTATION_CHANGED"):
        getattr(p, stage)(task)
    assert p.get_task(task)["answer"] is None


@pytest.mark.parametrize("mutation", ["facts", "task_id", "run_id", "answer", "answer_hash"])
def test_task_and_answer_corruption_never_returns_reconstructed_success(preview, mutation):
    p, _ = preview
    _, task, _ = complete(p)
    record = p.get_task(task)
    if mutation == "facts": record["facts"]["remaining_validity_working_days"] = 30
    elif mutation in ("task_id", "run_id"): record[mutation] = "0" * 32
    elif mutation == "answer": record["answer"]["published"] = True
    else: record["answer_sha256"] = "0" * 64
    with p.db(True) as db:
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(record), task))
    with pytest.raises(PreviewError): p.finalize(task)


def test_no_success_without_completed_task_or_duplicate_run(preview):
    p, _ = preview
    run = p.begin_run()
    with pytest.raises(PreviewError): p.end_run(run, True)
    with pytest.raises(PreviewError): p.begin_run()


def test_bearer_origin_and_unstarted_campaign_boundaries(preview):
    p, _ = preview
    with TestClient(create_app(p, "x" * 40), base_url="http://localhost:8783") as client:
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["publication_enabled"] is False
        assert set(health.json()["startup_source_sha256"]) == {"crossdoc_preview.py", "crossdoc_preview_server.py", "crossdoc_reasoning.py"}
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer " + "x" * 40,
                                          "Origin": "https://evil.invalid"}).status_code in (403, 421)
    assert p.counts()["native_runs"] == 0

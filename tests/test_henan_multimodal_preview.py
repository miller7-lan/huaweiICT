"""Synthetic bridge contracts only; no real candidate, network, OCR or model."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gov_mcp.crossdoc_preview import PreviewError
from gov_mcp.henan_multimodal import FACTS, PROFILE, HenanMultimodalWorkspace, integration_seed
from gov_mcp.henan_multimodal_preview import HENAN_SCENARIO, HenanMultimodalPreview, code_fingerprints
from gov_mcp.ontology_crossdoc_preview_server import create_app, open_preview
from test_henan_multimodal import ACTOR, FixtureSource, documents  # noqa: F401


@pytest.fixture
def integrated(tmp_path, documents):
    seed = json.loads((Path(__file__).resolve().parents[1] /
                       "config/preparation/ontology.seed.json").read_text("utf-8"))
    source = FixtureSource(documents)
    workspace = HenanMultimodalWorkspace.initialize(tmp_path / "synthetic-ontology", integration_seed(seed), source)
    baseline = workspace.status()["head"]
    candidate = workspace.submit()
    active = workspace.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    runtime = tmp_path / "synthetic-native"
    runtime.mkdir()
    preview = HenanMultimodalPreview(runtime, workspace, active)
    return preview, workspace, source, baseline, active


def stages(preview, verify=False):
    run = preview.begin_run()
    task = preview.open_query(HENAN_SCENARIO)["task_id"]
    preview.retrieve(task)
    if verify:
        preview.verify(task)
    return run, task


def test_three_documents_and_reviewed_four_node_plan_persist_exact_answer(integrated):
    preview, workspace, _, baseline, active = integrated
    assert preview.preflight()["document_count"] == 3
    run = preview.begin_run()
    with pytest.raises(PreviewError, match="FIXED_SCENARIO"):
        preview.open_query("nanjing_outside_old_card")
    opened = preview.open_query(HENAN_SCENARIO)
    task_id = opened["task_id"]
    assert opened["facts"] == FACTS
    retrieved = preview.retrieve(task_id)
    assert set(retrieved["documents"]) == {"catalog", "norm", "scan"}
    assert sum(len(doc["segments"]) for doc in retrieved["documents"].values()) == 17
    assert preview.verify(task_id)["verification_method"] == "reviewed_ontology_plan_execution"
    assert preview.state(task_id)["can_finalize"] is True
    answer = preview.finalize(task_id)
    proof = answer["reasoning"]
    assert answer["profile"] == PROFILE and answer["scenario_id"] == HENAN_SCENARIO
    assert answer["mode"] == "reviewed_henan_multimodal_archived_preview"
    assert [node["id"] for node in proof["trace"]] == ["catalog_basis", "fee_branch", "service_archive", "archive_answer"]
    assert [node["depends_on"] for node in proof["trace"]] == [[], ["catalog_basis"], [], ["fee_branch", "service_archive"]]
    assert proof["archive_result"]["ordinary_renewal_fee_yuan"] == 23  # synthetic counterfactual
    assert proof["archive_result"]["historical_duration"]["quoted_days"] == 29
    assert proof["archive_result"]["historical_duration"]["unit"] == "day_unspecified"
    assert proof["actual_fee_yuan"] is None and proof["promised_duration"] is None
    assert proof["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert proof["current_applicability"] == "unknown"
    assert answer["published"] is False and answer["formal_approval"] is False
    assert len(proof["trace"][-1]["evidence"]) == 17
    assert preview.get_task(task_id)["answer"] == answer
    preview.end_run(run, True)
    assert preview.finalize(task_id) == answer
    with pytest.raises(PreviewError, match="BUDGET_EXHAUSTED"):
        preview.begin_run()
    workspace.rollback(baseline, active, ACTOR, confirmed=True)
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        preview.finalize(task_id)


@pytest.mark.parametrize("stage", ["begin", "retrieve", "verify", "state", "finalize"])
def test_rollback_blocks_each_native_stage(integrated, stage):
    preview, workspace, _, baseline, active = integrated
    task = None
    if stage != "begin":
        preview.begin_run()
        task = preview.open_query(HENAN_SCENARIO)["task_id"]
        if stage in {"verify", "state", "finalize"}:
            preview.retrieve(task)
        if stage in {"state", "finalize"}:
            preview.verify(task)
    workspace.rollback(baseline, active, ACTOR, confirmed=True)
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        preview.begin_run() if stage == "begin" else getattr(preview, stage)(task)


@pytest.mark.parametrize("change", ["source_binding", "source_pause", "missing_scan", "bridge"])
def test_changed_source_pause_missing_modality_or_bridge_blocks_final_answer(integrated, monkeypatch, change):
    preview, _, source, _, _ = integrated
    run, task = stages(preview, verify=True)
    if change == "source_binding":
        source.binding = "2" * 64
    elif change == "source_pause":
        source.error = "SYNTHETIC_SOURCE_PAUSED"
    elif change == "missing_scan":
        del source.documents["scan"]
    else:
        monkeypatch.setattr(preview, "fingerprints", lambda: {"synthetic_bridge": "changed"})
    with pytest.raises(ValueError):
        preview.finalize(task)
    assert preview.get_task(task)["answer"] is None
    preview.end_run(run, False, "SYNTHETIC_CHANGED_SOURCE")


@pytest.mark.parametrize("stage", ["retrieve", "verify", "state", "finalize"])
def test_concurrent_terminal_run_blocks_stage_transaction(integrated, monkeypatch, stage):
    preview, _, _, _, _ = integrated
    run = preview.begin_run()
    task = preview.open_query(HENAN_SCENARIO)["task_id"]
    if stage != "retrieve":
        preview.retrieve(task)
    if stage in {"state", "finalize"}:
        preview.verify(task)
    original = preview._context

    def stop_after_context(task_id):
        result = original(task_id)
        preview.end_run(run, False, "SYNTHETIC_CONCURRENT_STOP")
        return result

    monkeypatch.setattr(preview, "_context", stop_after_context)
    with pytest.raises(PreviewError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"):
        getattr(preview, stage)(task)
    assert preview.get_task(task)["answer"] is None


def test_private_task_fields_never_return_and_changed_answer_is_rejected(integrated):
    preview, _, _, _, _ = integrated
    _, task = stages(preview, verify=True)
    original = deepcopy(preview.get_task(task))
    tampered = deepcopy(original)
    tampered["facts"]["applicant_id"] = "synthetic-private-never-return"
    with preview.db(True) as db:
        preview._put(db, tampered)
    with pytest.raises(PreviewError, match="FIXED_TASK_INPUT_CHANGED"):
        preview.finalize(task)
    with preview.db(True) as db:
        preview._put(db, original)
    preview.finalize(task)
    tampered = deepcopy(preview.get_task(task))
    tampered["answer"]["profile"] = "unreviewed-profile"
    with preview.db(True) as db:
        preview._put(db, tampered)
    with pytest.raises(PreviewError, match="PERSISTED_ANSWER_CHANGED"):
        preview.finalize(task)


def test_henan_health_pins_bridge_and_retains_bearer_origin_boundary(integrated):
    preview, _, _, _, _ = integrated
    fingerprints = code_fingerprints()
    assert {"henan_multimodal_preview.py", "henan_multimodal.py", "henan_multimodal_sources.py"} <= set(fingerprints)
    with TestClient(create_app(preview, "x" * 40), base_url="http://localhost:8785") as client:
        assert client.get("/health").json()["startup_source_sha256"] == fingerprints
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer " + "x" * 40,
                                          "Origin": "https://evil.invalid"}).status_code in (403, 421)
    assert preview.counts()["native_runs"] == 0


def test_server_dispatches_henan_source_binding_without_copying_originals(integrated, monkeypatch, tmp_path):
    preview, workspace, source, _, active = integrated
    import gov_mcp.henan_multimodal_sources as sources
    received = []

    def adapter(*paths):
        received.extend(paths)
        return source

    monkeypatch.setattr(sources, "ArchivedHenanMultimodalSource", adapter)
    paths = {key: str(tmp_path / key) for key in
             ("preparation_root", "observation_path", "extraction_path", "gates_path")}
    config = {"profile": PROFILE, "ontology_root": str(workspace.root), "expected_version": active,
              "henan_source": paths}
    (preview.root / "ontology-binding.json").write_text(json.dumps(config), "utf-8")
    opened = open_preview(preview.root)
    assert isinstance(opened, HenanMultimodalPreview)
    assert received == [Path(value) for value in paths.values()]
    assert opened.preflight()["scenario_id"] == HENAN_SCENARIO
    config["profile"] = "unregistered-profile"
    (preview.root / "ontology-binding.json").write_text(json.dumps(config), "utf-8")
    with pytest.raises(ValueError, match="UNRELATED_ONTOLOGY_EXECUTION_PROFILE"):
        open_preview(preview.root)

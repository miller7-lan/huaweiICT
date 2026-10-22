"""Synthetic contract checks; never evidence of a real native model run."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gov_mcp.crossdoc_preview import PreviewError, SCENARIO
from gov_mcp.ontology_crossdoc import ArchivedCrossdocSource, CrossdocOntologyWorkspace, integration_seed
from gov_mcp.ontology_crossdoc_preview import OntologyCrossdocPreview
from gov_mcp.ontology_crossdoc_preview_server import create_app
from test_crossdoc_reasoning import archive  # noqa: F401 -- isolated synthetic source fixture


ACTOR = {"kind": "human", "name": "Synthetic contract reviewer"}


@pytest.fixture
def integrated(archive):
    seed = json.loads((Path(__file__).resolve().parents[1] /
                       "config/preparation/ontology.seed.json").read_text("utf-8"))
    workspace = CrossdocOntologyWorkspace.initialize(
        archive / "ontology", integration_seed(seed), ArchivedCrossdocSource(archive))
    baseline, candidate = workspace.status()["head"], workspace.submit()
    active = workspace.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    preview = OntologyCrossdocPreview(archive, workspace, active)
    return preview, workspace, baseline, active, archive


def stages(preview):
    run = preview.begin_run()
    task = preview.open_query(SCENARIO)["task_id"]
    preview.retrieve(task)
    return run, task


def test_real_reviewed_plan_drives_proof_and_exact_persisted_answer(integrated, monkeypatch):
    p, workspace, baseline, active, _ = integrated
    # The original preview solver must not be used to compute this result.
    import gov_mcp.crossdoc_reasoning as original
    monkeypatch.setattr(original, "solve", lambda *_: (_ for _ in ()).throw(AssertionError("old solver")))
    run, task = stages(p)
    proof = p.verify(task)["reasoning"]
    assert proof["reason"] == "REVIEWED_ONTOLOGY_PLAN_EXECUTED"
    assert [hop["output"] for hop in proof["trace"]] == [25, 22, "false"]
    answer = p.finalize(task)
    assert answer["ontology_context"]["version"] == active
    assert all(hop["ontology_context_binding"] == answer["ontology_context"]["binding"]
               for hop in proof["trace"])
    assert p.get_task(task)["answer"] == answer and answer["published"] is False
    p.end_run(run, True)
    assert p.finalize(task) == answer
    workspace.rollback(baseline, active, ACTOR, confirmed=True)
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        p.finalize(task)


@pytest.mark.parametrize("stage", ["begin", "retrieve", "verify", "state", "finalize"])
def test_rollback_never_allows_a_pinned_native_stage(integrated, stage):
    p, workspace, baseline, active, _ = integrated
    task = None
    if stage != "begin":
        _, task = stages(p)
        if stage in {"state", "finalize"}:
            p.verify(task)
    workspace.rollback(baseline, active, ACTOR, confirmed=True)
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        p.begin_run() if stage == "begin" else getattr(p, stage)(task)
    if task:
        assert p.get_task(task)["answer"] is None


def test_current_source_pause_and_budget_remain_effective(integrated):
    p, _, _, _, archive = integrated
    run, task = stages(p)
    gates_path = archive / "gates.json"
    gates = json.loads(gates_path.read_text("utf-8"))
    gates["sources"]["cycle"]["paused_reason"] = "synthetic pause"
    gates_path.write_text(json.dumps(gates), "utf-8")
    with pytest.raises(ValueError, match="SOURCE_PAUSED"):
        p.verify(task)
    p.end_run(run, False, "SYNTHETIC_PAUSE")
    gates["sources"]["cycle"]["paused_reason"] = None
    gates_path.write_text(json.dumps(gates), "utf-8")
    with pytest.raises(PreviewError, match="BUDGET_EXHAUSTED"):
        p.begin_run()


def test_ontology_or_bridge_changes_invalidate_existing_task(integrated, monkeypatch):
    p, _, _, _, _ = integrated
    _, task = stages(p)
    import gov_mcp.ontology_crossdoc_preview as bridge
    original = bridge.code_fingerprints
    monkeypatch.setattr(bridge, "code_fingerprints", lambda: {**original(), "bridge": "changed"})
    with pytest.raises(PreviewError, match="SOURCE_OR_IMPLEMENTATION_CHANGED"):
        p.verify(task)


def test_task_tampering_does_not_return_a_fictional_answer(integrated):
    p, _, _, _, _ = integrated
    _, task = stages(p)
    p.verify(task)
    record = deepcopy(p.get_task(task))
    record["facts"]["private_answer"] = "never return"
    with p.db(True) as db:
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(record), task))
    with pytest.raises(PreviewError, match="FIXED_TASK_INPUT_CHANGED"):
        p.finalize(task)


def test_dedicated_surface_requires_bearer_and_rejects_foreign_origin(integrated):
    p, _, _, _, _ = integrated
    with TestClient(create_app(p, "x" * 40), base_url="http://localhost:8785") as client:
        health = client.get("/health").json()
        assert health["service"] == "govkb_ontology_crossdoc_preview"
        assert health["publication_enabled"] is False
        assert client.get("/sse").status_code == 401
        assert client.get("/sse", headers={"Authorization": "Bearer " + "x" * 40,
                                          "Origin": "https://evil.invalid"}).status_code in (403, 421)
    assert p.counts()["native_runs"] == 0


@pytest.mark.parametrize("stage", ["retrieve", "verify", "state", "finalize"])
def test_stop_between_context_and_write_transaction_blocks_stage(integrated, monkeypatch, stage):
    p, _, _, _, _ = integrated
    run = p.begin_run()
    task = p.open_query(SCENARIO)["task_id"]
    if stage != "retrieve":
        p.retrieve(task)
    if stage in {"state", "finalize"}:
        p.verify(task)
    original = p._context

    def concurrent_stop(task_id):
        result = original(task_id)
        p.end_run(run, False, "SYNTHETIC_CONCURRENT_STOP")
        return result

    monkeypatch.setattr(p, "_context", concurrent_stop)
    with pytest.raises(PreviewError, match="AUTHORIZED_ACTIVE_RUN_REQUIRED"):
        getattr(p, stage)(task)
    assert p.get_task(task)["answer"] is None

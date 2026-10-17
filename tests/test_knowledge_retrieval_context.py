"""Synthetic development-context binding; no real source or model approval."""

import copy
import json
from datetime import datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore
import prepare_knowledge_retrieval_trial as preparation
from prepare_knowledge_retrieval_trial import selected_evidence
from verify_knowledge_source_units import build_units


@pytest.fixture
def context(tmp_path):
    writer = KnowledgeStore.initialize(tmp_path/"synthetic-context")
    config = {"id": "synthetic", "label": "模拟来源", "origin": "https://fixtures.invalid",
        "allowed_paths": ["/doc"], "seeds": ["https://fixtures.invalid/doc"], "enabled": True,
        "interval_seconds": 3600, "delay_seconds": 5,
        "metadata": {"title": "模拟资料", "region": "synthetic", "matter": "synthetic", "document_type": "synthetic",
            "effective_from": None, "effective_to": None, "published_at": None, "usage_basis": "Self-authored synthetic text"},
        "access_review": {"approved": True, "actor_kind": "synthetic", "reviewer": "synthetic-only",
            "usage_basis": "No network; self authored", "checked_at": datetime.now(timezone.utc).isoformat()}}
    writer.register(config)
    chunks = [{"locator": "PDF第1页", "category": "procedure", "text": "一、模拟办理周期\n从受理开始"},
        {"locator": "PDF第2页", "category": "procedure", "text": "二十个自然日。"}]
    revision = writer.ingest("synthetic", config["seeds"][0], b"synthetic-only", config["metadata"], chunks)
    reader = KnowledgeStore(writer.root, read_only=True)
    with reader.db() as db:
        row, _ = reader._verify(db, revision, require_review=False)
    corpus = [{"revision_id": revision, "source_id": "synthetic", "locator": c["locator"], "category": c["category"], "quote": c["text"]} for c in chunks]
    units, _ = build_units(corpus, [{"revision_id": revision, "source_id": "synthetic", "body_sha256": row["body_sha256"]}])
    selected = [{"unit_id": u["id"], **{k: u[k] for k in ("revision_id", "source_id", "spans", "text_sha256")}} for u in units]
    return writer, reader, units, selected, revision


def test_selected_context_keeps_cross_page_text_as_separate_exact_fragments(context):
    _, reader, units, selected, _ = context
    evidence = selected_evidence(reader, selected, units)
    assert len(evidence) == 2 and len({e["evidence_id"] for e in evidence}) == 2
    assert [e["text"] for e in evidence] == [p["text"] for u in units for p in u["parts"]]
    assert all(e["current_applicability"] == "unknown" and e["source_unit_proposal_unapproved"] is True for e in evidence)
    assert all(e["review_state"] == "pending_review" for e in evidence)


@pytest.mark.parametrize("changed", ["selection", "part"])
def test_context_refuses_changed_selection_or_source_part(context, changed):
    _, reader, units, selected, _ = context
    units, selected = copy.deepcopy(units), copy.deepcopy(selected)
    if changed == "selection":selected[0]["spans"][0]["range"][0] += 1
    else:units[0]["parts"][0]["text"] = "伪造片段"
    with pytest.raises(ValueError, match="不匹配"):
        selected_evidence(reader, selected, units)


def test_context_refuses_superseded_revision_and_writable_connection(context):
    writer, reader, units, selected, revision = context
    with pytest.raises(ValueError, match="只读"):
        selected_evidence(writer, selected, units)
    with reader.db() as db:
        row, _ = reader._verify(db, revision, require_review=False)
    metadata = json.loads(row["metadata"])
    writer.amend(revision, dict(metadata, title="模拟资料新修订"))
    with pytest.raises(ValueError, match="当前待复核"):
        selected_evidence(reader, selected, units)


def test_context_does_not_export_gold_annotations_attached_to_selection(context):
    _, reader, units, selected, _ = context
    selected = [dict(s, ai_reference_answer="GOLD_SECRET", expected_behavior="GOLD_ACTION", missing_targets="GOLD_TARGET") for s in selected]
    assert "GOLD_" not in json.dumps(selected_evidence(reader, selected, units))


def test_offline_descriptor_uses_frozen_metadata_without_claiming_live_check(context, monkeypatch):
    _, reader, _, _, _ = context
    (reader.root/"consultation-agent-state.json").write_text(json.dumps({"active_model": "synthetic-model"}), encoding="utf-8")
    frozen = {"model": "synthetic-model", "model_digest": "synthetic-digest", "model_definition_sha256": "synthetic-definition"}
    monkeypatch.setattr(preparation.preview, "load_manifest", lambda run: (None, frozen))
    def no_http(*args, **kwargs):raise AssertionError("Offline preparation cannot contact a model service")
    monkeypatch.setattr(preparation.httpx, "Client", no_http)
    actual = preparation.model_descriptor(reader, "synthetic-frozen-run")
    assert actual["source_kind"] == "prior_frozen_manifest" and actual["server_checked"] is False
    assert actual["digest"] == frozen["model_digest"]


def test_offline_descriptor_cannot_switch_configured_model(context, monkeypatch):
    _, reader, _, _, _ = context
    (reader.root/"consultation-agent-state.json").write_text(json.dumps({"active_model": "configured"}), encoding="utf-8")
    monkeypatch.setattr(preparation.preview, "load_manifest", lambda run: (None, {"model": "different"}))
    with pytest.raises(ValueError, match="不能切换"):
        preparation.model_descriptor(reader, "synthetic-frozen-run")


def test_offline_run_rechecks_definition_before_generation(tmp_path, monkeypatch):
    manifest = {"model": "synthetic-model", "model_digest": "known", "model_server_checked_at_preparation": False,
        "model_definition_sha256": "different-frozen-definition", "cases": []}
    monkeypatch.setattr(preparation.preview, "load_manifest", lambda run: (tmp_path, manifest))
    calls = []
    class Response:
        def __init__(self, value):self.value = value
        def raise_for_status(self):return self
        def json(self):return self.value
    class Client:
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def get(self, url):return Response({"models": [{"name": "synthetic-model", "digest": "known"}]})
        def post(self, url, json):
            calls.append(url)
            assert url.endswith("/api/show")
            return Response({"different": "definition"})
    monkeypatch.setattr(preparation.preview.httpx, "Client", lambda **kwargs: Client())
    with pytest.raises(ValueError, match="未发送生成请求"):
        preparation.preview.run("synthetic-run")
    assert len(calls) == 1 and not list(tmp_path.glob("KQ*.json"))

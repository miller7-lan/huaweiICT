"""Synthetic annotation provenance fixtures; no official business answers or network calls."""
from copy import deepcopy

import pytest

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_notes import bind_scope_notes


def seal(value):
    value["content_sha256"] = fingerprint({k: v for k, v in value.items() if k != "content_sha256"})
    return value


@pytest.fixture
def fixture(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "fixture")
    metadata = {"title": "合成来源", "region": "synthetic", "matter": "synthetic", "document_type": "synthetic",
                "effective_from": "2026-01-01", "effective_to": None, "usage_basis": "Synthetic test only"}
    config = {"id": "fixture", "label": "合成来源", "enabled": False, "origin": "https://example.gov.cn",
              "allowed_paths": ["/"], "seeds": ["https://example.gov.cn/a"], "interval_seconds": 3600,
              "delay_seconds": 5, "metadata": metadata}
    store.register(config)
    chunk = {"locator": "合成段落1", "category": "synthetic", "text": "合成正文；不代表任何真实规则。"}
    rid = store.ingest("fixture", config["seeds"][0], b"synthetic raw", metadata, [chunk])
    with store.db() as db:
        row, _ = store._verify(db, rid, require_review=False)
    scope = fingerprint({"scope": "synthetic"})
    note = {"id": "fixture-note", "scope_sha256": scope, "reviewer_kind": "ai", "human_review_completed": False,
            "business_approval": False, "reference_oracle": False, "current_eligibility_certified": False,
            "sources": {"A": {"source_id": "fixture", "url": config["seeds"][0], "revision": rid,
                "original_sha256": row["sha256"], "body_sha256": row["body_sha256"],
                "metadata_sha256": fingerprint(row["metadata"].encode()), "state": "pending_review"}},
            "anchors": [{"id": "A1", "source_key": "A", "ordinal": 0, "locator": chunk["locator"], "text_sha256": fingerprint(chunk["text"].encode())}],
            "facts": [{"finding": "合成待核观察", "limits": "仅工程测试，不是政策判断", "anchor_ids": ["A1"]}]}
    artifact = seal({"schema": 1, "kind": "source_scope_notes_not_approval_or_oracle", "notes": [note]})
    return store, metadata, chunk, scope, artifact


def test_bound_note_is_a_copy_and_never_mutates_source_or_approval(fixture):
    store, _, _, scope, artifact = fixture
    before = fingerprint(store.path.read_bytes())
    result = bind_scope_notes(store, artifact, scope)
    assert result["status"] == "bound" and result["business_approval"] is False
    assert result["notes"][0] == artifact["notes"][0]
    result["notes"][0]["facts"][0]["finding"] = "changed returned copy"
    assert artifact["notes"][0]["facts"][0]["finding"] == "合成待核观察"
    assert fingerprint(store.path.read_bytes()) == before
    assert store.status()["counts"]["versions"] == store.status()["counts"]["heads"] == 0


def test_missing_notes_are_explicit(fixture):
    store, _, _, scope, _ = fixture
    result = bind_scope_notes(store, None, scope)
    assert result["status"] == "missing" and result["notes"] == []


def test_changed_analysis_requires_new_artifact_digest(fixture):
    store, _, _, scope, artifact = fixture
    artifact["notes"][0]["facts"][0]["finding"] = "unsealed edit"
    result = bind_scope_notes(store, artifact, scope)
    assert result["status"] == "invalid" and result["notes"] == []


def test_changed_scope_withholds_all_interdependent_analysis(fixture):
    store, _, _, _, artifact = fixture
    result = bind_scope_notes(store, artifact, "different scope")
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "scope_changed"


@pytest.mark.parametrize("field,value", [("business_approval", True), ("reference_oracle", True),
    ("human_review_completed", True), ("current_eligibility_certified", True), ("reviewer_kind", "human")])
def test_scope_annotation_cannot_carry_source_approval_or_test_acceptance(fixture, field, value):
    store, _, _, scope, artifact = fixture
    artifact["notes"][0][field] = value
    result = bind_scope_notes(store, seal(artifact), scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "annotation_cannot_carry_approval"


@pytest.mark.parametrize("field,value", [("url", "https://example.gov.cn/other"), ("source_id", "other"),
    ("original_sha256", "wrong"), ("body_sha256", "wrong"), ("metadata_sha256", "wrong"), ("state", "reviewed")])
def test_each_source_identity_component_is_bound(fixture, field, value):
    store, _, _, scope, artifact = fixture
    artifact["notes"][0]["sources"]["A"][field] = value
    result = bind_scope_notes(store, seal(artifact), scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "source_binding_changed"


def test_historical_original_does_not_make_superseded_annotation_current(fixture):
    store, metadata, chunk, scope, artifact = fixture
    store.ingest("fixture", "https://example.gov.cn/a", b"synthetic new raw", metadata, [dict(chunk, text="合成改变后的正文")])
    result = bind_scope_notes(store, artifact, scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "source_superseded"


def test_review_state_change_invalidates_pre_review_note(fixture):
    store, _, _, scope, artifact = fixture
    store.review(artifact["notes"][0]["sources"]["A"]["revision"], "synthetic reviewer", confirmed=True)
    result = bind_scope_notes(store, artifact, scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "source_binding_changed"


def test_corrupt_original_cannot_supply_bound_analysis(fixture):
    store, _, _, scope, artifact = fixture
    sha = artifact["notes"][0]["sources"]["A"]["original_sha256"]
    (store.root / "originals" / sha[:2] / sha).write_bytes(b"corrupt synthetic bytes")
    result = bind_scope_notes(store, artifact, scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "source_or_annotation_invalid"


@pytest.mark.parametrize("field,value", [("text_sha256", "wrong"), ("locator", "wrong"), ("ordinal", -1), ("ordinal", True)])
def test_literal_anchor_corruption_is_withheld(fixture, field, value):
    store, _, _, scope, artifact = fixture
    artifact["notes"][0]["anchors"][0][field] = value
    assert bind_scope_notes(store, seal(artifact), scope)["notes"] == []


def test_fact_cannot_point_to_an_unknown_anchor(fixture):
    store, _, _, scope, artifact = fixture
    artifact["notes"][0]["facts"][0]["anchor_ids"] = ["unbound"]
    result = bind_scope_notes(store, seal(artifact), scope)
    assert result["notes"] == [] and result["withheld"][0]["reason"] == "fact_reference_invalid"


def test_invalid_note_is_withheld_without_hiding_an_independent_valid_note(fixture):
    store, _, _, scope, artifact = fixture
    second = deepcopy(artifact["notes"][0])
    second.update(id="second", scope_sha256="different")
    artifact["notes"].append(second)
    result = bind_scope_notes(store, seal(artifact), scope)
    assert result["status"] == "partially_bound" and len(result["notes"]) == len(result["withheld"]) == 1


def test_duplicate_note_ids_are_invalid(fixture):
    store, _, _, scope, artifact = fixture
    artifact["notes"].append(deepcopy(artifact["notes"][0]))
    result = bind_scope_notes(store, seal(artifact), scope)
    assert result["status"] == "invalid" and result["notes"] == []

"""Provenance migration must not silently change test meanings or source spans."""
import sqlite3
from copy import deepcopy

import pytest

from gov_mcp.knowledge import fingerprint
from holdout_provenance import full_anchor, migration_path
from holdout_provenance_v4 import MIGRATION_V4, literal_anchors, validate_v4_semantics


def drafts():
    before = {"draft_revision":3, "author":{"name":"fixture AI"}, "sources":{},
        "independent_acceptance_ready":False, "independence_certified":False,
        "human_review_completed":False, "ready_for_business_publication":False,
        "reference_frozen_at":None, "snapshot_binding":None, "observed_run":None,
        "freeze_blockers":["sources", "independence", "snapshot", "run"],
        "cases":[{"id":str(i), "question":f"fixture question {i}", "query_date":"2026-10-16",
            "reference_answer_draft":"unapproved oracle", "role":"citizen", "category":"fixture",
            "proposed_outcome_after_qualified_publication":"stop", "unresolved_prerequisites":["pending"],
            "background_source_anchors":[], "proposed_relevant_evidence":[]} for i in range(30)]}
    after = deepcopy(before)
    after.update(draft_revision=4, provenance_migration={"path":MIGRATION_V4.as_posix()}, provenance_migrated_at="fixture")
    return before, after


def test_only_provenance_may_change():
    before, after = drafts()
    after["cases"][0]["background_source_anchors"] = [{"revision":"fixture"}]
    validate_v4_semantics(before, after)


@pytest.mark.parametrize("field,value", [("question","changed"), ("query_date","2026-10-17"),
    ("reference_answer_draft","tuned"), ("role","staff"), ("category","different"),
    ("proposed_outcome_after_qualified_publication","answer"), ("unresolved_prerequisites",[])])
def test_semantic_changes_cannot_be_hidden_by_rebinding(field, value):
    before, after = drafts()
    after["cases"][0][field] = value
    with pytest.raises(ValueError, match="cannot change"):
        validate_v4_semantics(before, after)


@pytest.mark.parametrize("field,value", [("freeze_blockers",[]), ("independence_certified",True),
    ("reference_frozen_at","now"), ("author",{"name":"other"}), ("extra_approval",True)])
def test_blockers_identity_and_acceptance_cannot_change(field, value):
    before, after = drafts()
    after[field] = value
    with pytest.raises(ValueError, match="cannot change"):
        validate_v4_semantics(before, after)


def test_migration_path_is_version_bound():
    _, after = drafts()
    assert migration_path(after) == MIGRATION_V4
    after["provenance_migration"] = {"path":"../../arbitrary.json"}
    with pytest.raises(ValueError, match="path"):
        migration_path(after)


@pytest.fixture
def anchors():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE chunks(revision_id TEXT,ordinal INTEGER,locator TEXT,text TEXT)")
    for revision, ordinal in [("old",5), ("new",4)]:
        db.execute("INSERT INTO chunks VALUES(?,?,?,?)", (revision, ordinal, "clause", "complete literal paragraph"))
    case = {"background_source_anchors":[full_anchor(db,"NATIONAL","old","clause")]}
    yield db, case, {"NATIONAL":{"revision":"new"}}
    db.close()


def test_ordinal_move_preserves_exact_parent_and_span(anchors):
    db, case, sources = anchors
    result = literal_anchors(db, case, sources)
    expected = deepcopy(case["background_source_anchors"])
    expected[0].update(revision="new", ordinal=4)
    assert result == expected


@pytest.mark.parametrize("change", ["parent", "locator", "duplicate", "historical_span"])
def test_changed_or_ambiguous_source_cannot_be_rebound(anchors, change):
    db, case, sources = anchors
    if change == "parent":
        db.execute("UPDATE chunks SET text='complete literal paragraph plus' WHERE revision_id='new'")
    elif change == "locator":
        db.execute("UPDATE chunks SET locator='other' WHERE revision_id='new'")
    elif change == "duplicate":
        db.execute("INSERT INTO chunks SELECT revision_id,9,locator,text FROM chunks WHERE revision_id='new'")
    else:
        case["background_source_anchors"][0].update(start=1,span_sha256=fingerprint(b"omplete literal paragraph"))
    with pytest.raises(ValueError):
        literal_anchors(db, case, sources)

"""Provenance edits cannot smuggle changed answers into a design review."""

import json
from copy import deepcopy

import pytest

from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_review import question_fingerprint
from holdout_provenance import MIGRATION, migration_path, semantic_projection, validate_semantics


def drafts():
    before = {"draft_revision":2,"author":{"name":"fixture AI"},"sources":{"fixture":{"revision":"old"}},
        "independent_acceptance_ready":False,"independence_certified":False,"human_review_completed":False,
        "ready_for_business_publication":False,"reference_frozen_at":None,"snapshot_binding":None,"observed_run":None,
        "freeze_blockers":["sources","independence","snapshot","run"],
        "cases":[{"id":str(i),"question":f"synthetic question {i}","query_date":"2026-10-16",
                  "role":"citizen","category":"fixture","reference_answer_draft":"synthetic unapproved oracle",
                  "proposed_outcome_after_qualified_publication":"stop","unresolved_prerequisites":["unresolved"],
                  "background_source_anchors":[{"revision":"old"}],"proposed_relevant_evidence":[]} for i in range(30)]}
    after = deepcopy(before)
    after.update(draft_revision=3,provenance_migration={"path":MIGRATION.as_posix()},provenance_migrated_at="fixture")
    after["cases"][0]["background_source_anchors"] = [{"revision":"new"}]
    return before,after


def test_provenance_change_keeps_semantics():
    before,after = drafts()
    validate_semantics(before,after)
    assert semantic_projection(before) == semantic_projection(after)


@pytest.mark.parametrize("field,value",[("question","other"),("query_date","2026-10-17"),
    ("role","staff"),("category","changed"),("reference_answer_draft","tuned from output"),
    ("proposed_outcome_after_qualified_publication","answer"),("unresolved_prerequisites",[])])
def test_semantic_edits_are_not_provenance(field,value):
    before,after = drafts();after["cases"][0][field] = value
    with pytest.raises(ValueError,match="cannot change"):
        validate_semantics(before,after)


@pytest.mark.parametrize("field,value",[("author",{"name":"different"}),
    ("independence_certified",True),("reference_frozen_at","now"),("unexpected_approval",True)])
def test_migration_cannot_change_authorship_or_acceptance(field,value):
    before,after = drafts();after[field] = value
    with pytest.raises(ValueError,match="cannot change"):
        validate_semantics(before,after)


@pytest.fixture(params=[3, 4])
def screened(tmp_path,monkeypatch,request):
    import export_knowledge_review_packet as exporter
    _,after = drafts()
    if request.param == 4:
        from holdout_provenance_v4 import MIGRATION_V4
        after.update(draft_revision=4,provenance_migration={"path":MIGRATION_V4.as_posix()})
    after["content_sha256"] = fingerprint(after)
    directory = tmp_path / "evaluation/knowledge";directory.mkdir(parents=True)
    migration = {"kind":"synthetic migration"};migration["content_sha256"] = fingerprint(migration)
    (tmp_path/migration_path(after)).write_text(json.dumps(migration),encoding="utf-8")
    questions = [{"id":c["id"],"query_date":c["query_date"],"question_sha256":question_fingerprint(c["question"])} for c in after["cases"]]
    screen = {"kind":"ai_semantic_screening_not_acceptance","batch_ready_for_freeze":False,
        "independence_certified":False,"business_acceptance_executed":False,"cases":questions,
        "question_set_sha256":fingerprint(questions),"development_registry_sha256":fingerprint([]),
        "development_sources":[],"history_artifacts":[],"reference_contents_sha256":after["content_sha256"],
        "reference_semantics_sha256":fingerprint(semantic_projection(after)),"provenance_migration_sha256":migration["content_sha256"]}
    screen["content_sha256"] = fingerprint(screen)
    (directory/"holdout-semantic-screening.json").write_text(json.dumps(screen),encoding="utf-8")
    monkeypatch.setattr(exporter,"ROOT",tmp_path)
    assert exporter.semantic_screening_for_review(after,[])["status"] == "bound"
    return exporter,after,tmp_path


@pytest.mark.parametrize("change",["oracle","source","outcome","migration"])
def test_rehashing_modified_content_does_not_reuse_screening(screened,change):
    exporter,after,root = screened
    if change == "oracle":after["cases"][0]["reference_answer_draft"] = "changed"
    elif change == "source":after["sources"]["fixture"]["revision"] = "different"
    elif change == "outcome":after["cases"][0]["proposed_outcome_after_qualified_publication"] = "answer"
    else:
        changed = {"kind":"different synthetic migration"};changed["content_sha256"] = fingerprint(changed)
        (root/migration_path(after)).write_text(json.dumps(changed),encoding="utf-8")
    after["content_sha256"] = fingerprint({k:v for k,v in after.items() if k != "content_sha256"})
    result = exporter.semantic_screening_for_review(after,[])
    assert result["status"] == "stale" and result["assessment"] is None and not result["batch_ready_for_freeze"]

"""Literal-only v3-to-v4 provenance migration; source review is not acceptance."""
from copy import deepcopy
from pathlib import Path

from gov_mcp.knowledge import fingerprint
from holdout_provenance import (TOP_FIELDS, full_anchor, read, sealed, semantic_projection,
                                source_binding)


ARCHIVE_V3 = Path("evaluation/knowledge/archive/holdout-v3-before-bound-source-review-20261017")
MIGRATION_V4 = Path("evaluation/knowledge/holdout-provenance-migration-v4.json")
BASE_COMMIT = "82235890470e0cc4464414be6d9223ed33623084"
EXPECTED_REVISIONS = {
    "NATIONAL": "rev-54a85427f0fe78c051faa026",
    "F18": "rev-e41c61067fe70ce4ce221dbf",
    "WINDOW": "rev-49ae68807f33bf53dbbec739",
}
ARCHIVE_NAMES = {"holdout-business-reference-draft.json", "holdout-business-questions-draft.json",
                 "holdout-semantic-screening.json", "holdout-draft-integrity.json"}


def archive_v3(root):
    manifest = read(root/ARCHIVE_V3/"manifest.json")
    if (not sealed(manifest) or manifest.get("source_commit") != BASE_COMMIT
            or {Path(item["path"]).name for item in manifest["files"]} != ARCHIVE_NAMES
            or len(manifest["files"]) != len(ARCHIVE_NAMES)):
        raise ValueError("Expected complete, sealed v3 archive")
    for item in manifest["files"]:
        path = (root/item["path"]).resolve()
        if path.parent != (root/ARCHIVE_V3).resolve() or fingerprint(path.read_bytes()) != item["sha256"]:
            raise ValueError("Archived v3 bytes differ")
    before = read(root/ARCHIVE_V3/"holdout-business-reference-draft.json")
    if not sealed(before) or before.get("draft_revision") != 3:
        raise ValueError("Expected intact v3 reference")
    return before, manifest


def validate_v4_semantics(before, after):
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    if (before.get("draft_revision") != 3 or after.get("draft_revision") != 4
            or not changed <= TOP_FIELDS or semantic_projection(before) != semantic_projection(after)
            or len(before["cases"]) != 30 or len({case["id"] for case in before["cases"]}) != 30
            or after.get("freeze_blockers") != before.get("freeze_blockers")
            or any(after.get(k) is not False for k in ("independent_acceptance_ready", "independence_certified",
                    "human_review_completed", "ready_for_business_publication"))
            or any(after.get(k) is not None for k in ("reference_frozen_at", "snapshot_binding", "observed_run"))):
        raise ValueError("v4 provenance cannot change questions, oracles, blockers or acceptance")


def literal_anchors(db, before_case, after_sources):
    anchors = []
    for original in before_case["background_source_anchors"]:
        if original != full_anchor(db, original["source_key"], original["revision"], original["locator"]):
            raise ValueError("Expected an intact complete historical source span")
        anchor = deepcopy(original)
        if anchor["source_key"] in EXPECTED_REVISIONS:
            revision = after_sources[anchor["source_key"]]["revision"]
            matches = [row for row in db.execute("SELECT ordinal,locator,text FROM chunks WHERE revision_id=?", (revision,))
                       if row["locator"] == anchor["locator"] and fingerprint(row["text"].encode()) == anchor["parent_text_sha256"]
                       and fingerprint(row["text"][anchor["start"]:anchor["end"]].encode()) == anchor["span_sha256"]]
            if len(matches) != 1:
                raise ValueError("Each v4 anchor requires one unchanged literal parent and span")
            anchor.update(revision=revision, ordinal=matches[0]["ordinal"])
        anchors.append(anchor)
    return anchors


def v4_evidence(store, db, before, after):
    validate_v4_semantics(before, after)
    if (set(before["sources"]) != set(after["sources"])
            or any(before["sources"][key] != after["sources"][key] for key in before["sources"] if key not in EXPECTED_REVISIONS)):
        raise ValueError("Only NATIONAL, F18 and WINDOW provenance may migrate")
    comparisons = {}
    for key, revision in EXPECTED_REVISIONS.items():
        old, new = before["sources"][key], after["sources"][key]
        if (new["revision"] != revision or (old["source_id"], old["url"]) != (new["source_id"], new["url"])
                or old["original_sha256"] != new["original_sha256"]
                or source_binding(store, db, new["source_id"], new["url"], revision) != new
                or new["business_approval"] is not True):
            raise ValueError("Migrated source requires its actual scoped review and original")
        old_row, old_chunks = store._content(db, old["revision"])
        new_row, new_chunks = store._content(db, revision)
        store._verify(db, old_row["id"], require_review=False)
        if key == "NATIONAL":
            if (len(old_chunks) != 11 or len(new_chunks) != 10
                    or [c for c in old_chunks if c["locator"] != "第七十六条"] != new_chunks):
                raise ValueError("Only the unreferenced national window clause may be removed")
            watch, watch_chunks = store._content(db, "rev-84412d6b0b512caf0cbe1a01")
            store._verify(db, watch["id"], require_review=False)
            if (watch["state"] != "pending_review" or watch["sha256"] != old_row["sha256"]
                    or watch_chunks != [c for c in old_chunks if c["locator"] == "第七十六条"]):
                raise ValueError("The unresolved national window clause must remain separately pending")
            comparisons[key] = {"kind": "same_original_ten_complete_clauses", "removed_unreferenced_locator": "第七十六条",
                                "pending_window_watch_revision": watch["id"]}
        else:
            if old_chunks != new_chunks or len(new_chunks) != 1:
                raise ValueError("Fee and service-window literal selections must remain identical")
            comparisons[key] = {"kind": "same_original_same_complete_paragraph", "locator": new_chunks[0]["locator"]}
        comparisons[key].update(before_revision=old["revision"], after_revision=revision,
            original_sha256=new["original_sha256"], before_body_sha256=old_row["body_sha256"], after_body_sha256=new_row["body_sha256"],
            actual_review_digest=new["recorded_review"]["digest"])
    cases = []
    for old, new in zip(before["cases"], after["cases"], strict=True):
        expected = literal_anchors(db, old, after["sources"])
        relevant = [] if old["proposed_outcome_after_qualified_publication"] == "stop" else [{"revision":a["revision"], "locator":a["locator"]} for a in expected]
        if new["background_source_anchors"] != expected or new["proposed_relevant_evidence"] != relevant:
            raise ValueError("v4 cannot insert, remove or alter evidence spans")
        cases.append({"id":old["id"], "before_sha256":fingerprint(old), "after_sha256":fingerprint(new),
                      "changed_fields":sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))})
    return {"before_reference_sha256":before["content_sha256"], "after_reference_sha256":after["content_sha256"],
            "case_semantics_sha256":fingerprint(semantic_projection(after)), "sources":comparisons, "cases":cases,
            "literal_span_changes":0, "evidence_insertions_or_removals":0}


def validate_v4_migration(root, store, db, reference):
    if reference.get("provenance_migration") != {"path":MIGRATION_V4.as_posix()} or not sealed(reference):
        raise ValueError("Expected explicitly bound v4 migration")
    before, manifest = archive_v3(root)
    from holdout_provenance import validate_migration
    historical_baseline = validate_migration(root, store, db, before)
    stale_path = ARCHIVE_V3/"holdout-draft-integrity.json"
    stale = read(root/stale_path)
    ledger = read(root/MIGRATION_V4)
    if (not sealed(ledger) or ledger.get("kind") != "holdout_provenance_migration_not_acceptance"
            or ledger.get("base_archive_manifest_sha256") != manifest["content_sha256"]
            or ledger.get("human_review_completed") is not False or ledger.get("acceptance_authorized") is not False
            or ledger.get("reviewer") != "Codex AI"
            or ledger.get("previous_integrity_report") != {"path":stale_path.as_posix(), "sha256":fingerprint((root/stale_path).read_bytes())}
            or stale.get("draft_integrity_passed") is not False
            or sorted(k for k,v in stale["checks"].items() if not v) != sorted(["latest_originals_and_literal_anchor_spans_match", "actual_export_contains_both_drafts_and_preserves_empty_approvals"])
            or ledger.get("evidence") != v4_evidence(store, db, before, reference)):
        raise ValueError("v4 ledger differs from the actual scoped reviews or unchanged semantics")
    return historical_baseline

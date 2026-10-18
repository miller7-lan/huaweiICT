"""Bind existing privacy anchors to their actual scoped review, retaining v4."""
from pathlib import Path

from gov_mcp.knowledge import fingerprint
from holdout_provenance import TOP_FIELDS, read, sealed, semantic_projection, source_binding
from holdout_provenance_v4 import ARCHIVE_NAMES, literal_anchors


ARCHIVE_V4 = Path("evaluation/knowledge/archive/holdout-v4-before-privacy-binding-20261018")
MIGRATION_V5 = Path("evaluation/knowledge/holdout-provenance-migration-v5.json")
BASE_COMMIT = "ffddda7e56eb9fa1766fda252df072a38c55eff1"
EXPECTED_REVISIONS = {"USE": "rev-f48eee88ddb882b4933b37de"}


def archive_v4(root):
    manifest = read(root/ARCHIVE_V4/"manifest.json")
    if (not sealed(manifest) or manifest.get("source_commit") != BASE_COMMIT
            or {Path(item["path"]).name for item in manifest["files"]} != ARCHIVE_NAMES
            or len(manifest["files"]) != len(ARCHIVE_NAMES)):
        raise ValueError("Expected complete, sealed v4 archive")
    for item in manifest["files"]:
        path = (root/item["path"]).resolve()
        if path.parent != (root/ARCHIVE_V4).resolve() or fingerprint(path.read_bytes()) != item["sha256"]:
            raise ValueError("Archived v4 bytes differ")
    before = read(root/ARCHIVE_V4/"holdout-business-reference-draft.json")
    if not sealed(before) or before.get("draft_revision") != 4:
        raise ValueError("Expected intact v4 reference")
    return before, manifest


def validate_v5_semantics(before, after):
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    if (before.get("draft_revision") != 4 or after.get("draft_revision") != 5
            or not changed <= TOP_FIELDS or semantic_projection(before) != semantic_projection(after)
            or len(before["cases"]) != 30 or len({c["id"] for c in before["cases"]}) != 30
            or after.get("freeze_blockers") != before.get("freeze_blockers")
            or any(after.get(k) is not False for k in ("independent_acceptance_ready", "independence_certified",
                "human_review_completed", "ready_for_business_publication"))
            or any(after.get(k) is not None for k in ("reference_frozen_at", "snapshot_binding", "observed_run"))):
        raise ValueError("v5 provenance cannot change questions, oracles, blockers or acceptance")


def v5_evidence(store, db, before, after):
    validate_v5_semantics(before, after)
    if (set(before["sources"]) != set(after["sources"])
            or any(before["sources"][key] != after["sources"][key] for key in before["sources"] if key != "USE")):
        raise ValueError("Only USE provenance may migrate")
    old, new = before["sources"]["USE"], after["sources"]["USE"]
    revision = EXPECTED_REVISIONS["USE"]
    if (old["revision"] != "rev-d22b05a6d353b71c8bc504ad" or new["revision"] != revision
            or (old["source_id"], old["url"]) != (new["source_id"], new["url"])
            or old["original_sha256"] != new["original_sha256"]
            or source_binding(store, db, new["source_id"], new["url"], revision) != new
            or new["business_approval"] is not True):
        raise ValueError("USE requires the actual scoped privacy review and same original")
    old_row, old_chunks = store._content(db, old["revision"])
    new_row, new_chunks = store._content(db, revision)
    store._verify(db, old_row["id"], require_review=False)
    selected = {"正文段落9", "正文段落10", "正文段落11", "正文段落12", "正文段落15"}
    if len(old_chunks) != 16 or len(new_chunks) != 5 or new_chunks != [c for c in old_chunks if c["locator"] in selected]:
        raise ValueError("Privacy selection must be exactly the five already reviewed literal paragraphs")
    cases = []
    for previous, current in zip(before["cases"], after["cases"], strict=True):
        expected = literal_anchors(db, previous, after["sources"], source_keys={"USE"})
        relevant = [] if previous["proposed_outcome_after_qualified_publication"] == "stop" else [
            {"revision":a["revision"], "locator":a["locator"]} for a in expected]
        if current["background_source_anchors"] != expected or current["proposed_relevant_evidence"] != relevant:
            raise ValueError("v5 cannot insert, remove or alter evidence spans")
        cases.append({"id":previous["id"], "before_sha256":fingerprint(previous), "after_sha256":fingerprint(current),
            "changed_fields":sorted(k for k in previous.keys() | current.keys() if previous.get(k) != current.get(k))})
    affected = [c["id"] for c in cases if c["before_sha256"] != c["after_sha256"]]
    if affected != ["HB21", "HB22", "HB38", "HB24"]:
        raise ValueError("Only the four existing privacy cases may be rebound")
    return {"before_reference_sha256":before["content_sha256"], "after_reference_sha256":after["content_sha256"],
        "case_semantics_sha256":fingerprint(semantic_projection(after)), "cases":cases,
        "sources":{"USE":{"kind":"same_original_five_reviewed_privacy_paragraphs", "before_revision":old["revision"],
            "after_revision":revision, "original_sha256":new["original_sha256"],
            "before_body_sha256":old_row["body_sha256"], "after_body_sha256":new_row["body_sha256"],
            "actual_review_digest":new["recorded_review"]["digest"]}},
        "literal_span_changes":0, "evidence_insertions_or_removals":0}


def validate_v5_migration(root, store, db, reference):
    if reference.get("provenance_migration") != {"path":MIGRATION_V5.as_posix()} or not sealed(reference):
        raise ValueError("Expected explicitly bound v5 migration")
    before, manifest = archive_v4(root)
    from holdout_provenance import validate_migration
    historical_baseline = validate_migration(root, store, db, before)
    stale_path = ARCHIVE_V4/"holdout-draft-integrity.json"
    stale, ledger = read(root/stale_path), read(root/MIGRATION_V5)
    if (not sealed(ledger) or ledger.get("kind") != "holdout_provenance_migration_not_acceptance"
            or ledger.get("base_archive_manifest_sha256") != manifest["content_sha256"]
            or ledger.get("human_review_completed") is not False or ledger.get("acceptance_authorized") is not False
            or ledger.get("reviewer") != "Codex AI"
            or ledger.get("previous_integrity_report") != {"path":stale_path.as_posix(), "sha256":fingerprint((root/stale_path).read_bytes())}
            or stale.get("draft_integrity_passed") is not False
            or sorted(k for k,v in stale["checks"].items() if not v) != sorted([
                "latest_originals_and_literal_anchor_spans_match", "actual_export_contains_both_drafts_and_preserves_empty_approvals"])
            or ledger.get("evidence") != v5_evidence(store, db, before, reference)):
        raise ValueError("v5 ledger differs from actual privacy review or unchanged semantics")
    return historical_baseline

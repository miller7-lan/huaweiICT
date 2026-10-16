"""Strict v2-to-v3 source migration; this never certifies reference truth."""

import json
import re
import unicodedata
from pathlib import Path

from gov_mcp.knowledge import fingerprint


ARCHIVE = Path("evaluation/knowledge/archive/holdout-v2-before-service-review-20261017")
MIGRATION = Path("evaluation/knowledge/holdout-provenance-migration-v3.json")
STALE_RECEIPT = Path("evidence/knowledge-update/archive/holdout-v2-stale-integrity-20261017.json")
PROVENANCE_FIELDS = {"background_source_anchors", "proposed_relevant_evidence"}
TOP_FIELDS = {"draft_revision", "sources", "cases", "provenance_migration",
              "provenance_migrated_at", "freeze_blockers", "content_sha256"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sealed(value):
    return fingerprint({k:v for k,v in value.items() if k != "content_sha256"}) == value.get("content_sha256")


def semantic_projection(reference):
    return [{k:v for k,v in c.items() if k not in PROVENANCE_FIELDS} for c in reference["cases"]]


def validate_semantics(before, after):
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    if (before["draft_revision"] != 2 or after["draft_revision"] != 3 or not changed <= TOP_FIELDS
            or semantic_projection(before) != semantic_projection(after)
            or len(before["cases"]) != 30 or len({c["id"] for c in before["cases"]}) != 30
            or any(after.get(k) is not False for k in ("independent_acceptance_ready", "independence_certified",
                                                       "human_review_completed", "ready_for_business_publication"))
            or any(after.get(k) is not None for k in ("reference_frozen_at", "snapshot_binding", "observed_run"))
            or after["freeze_blockers"][1:] != before["freeze_blockers"][1:]
            or len(after["freeze_blockers"]) != 4 or not after["freeze_blockers"][0]):
        raise ValueError("Provenance migration cannot change questions, oracles, design, authorship or acceptance")


def archive_reference(root):
    manifest = read(root / ARCHIVE / "manifest.json")
    if not sealed(manifest) or manifest["source_commit"] != "cad63175ba610148bed43b4d1dfb3627b545408e":
        raise ValueError("Expected intact v2 archive")
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if path.parent != (root / ARCHIVE).resolve() or fingerprint(path.read_bytes()) != item["sha256"]:
            raise ValueError("Archived v2 bytes differ")
    before = read(root / ARCHIVE / "holdout-business-reference-draft.json")
    if not sealed(before):
        raise ValueError("Archived v2 reference is damaged")
    return before, manifest


def source_binding(store, db, source_id, url, revision=None):
    sql = "SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=?"
    args = [source_id,url]
    if revision is not None:
        sql += " AND r.id=?"
        args.append(revision)
    row = db.execute(sql+" ORDER BY r.rowid DESC LIMIT 1", args).fetchone()
    if row is None or row["state"] not in ("pending_review", "reviewed"):
        raise ValueError("Expected available source candidate")
    approved = row["state"] == "reviewed"
    store._verify(db,row["id"],require_review=approved)
    result = {"source_id":source_id,"url":url,"revision":row["id"],"original_sha256":row["sha256"],
              "body_sha256":row["body_sha256"],"metadata_sha256":fingerprint(row["metadata"].encode()),
              "state":row["state"],"business_approval":approved}
    if approved:
        result["recorded_review"] = dict(db.execute("SELECT reviewer,digest,reviewed_at FROM reviews WHERE revision_id=?", (row["id"],)).fetchone())
    return result


def full_anchor(db, key, revision, locator):
    chunk = db.execute("SELECT ordinal,text FROM chunks WHERE revision_id=? AND locator=?", (revision,locator)).fetchone()
    if chunk is None:
        raise ValueError("Migration requires a literal source locator")
    text = chunk["text"]
    return {"source_key":key,"revision":revision,"locator":locator,"ordinal":chunk["ordinal"],
            "start":0,"end":len(text),"parent_text_sha256":fingerprint(text.encode()),"span_sha256":fingerprint(text.encode())}


def expected_anchors(db, before_case, after_sources):
    result = []
    for anchor in before_case["background_source_anchors"]:
        if before_case["id"] == "HB06" and (anchor["source_key"],anchor["locator"]) == ("D26","正文段落2"):
            continue
        if anchor["source_key"] in ("D26", "LAW"):
            result.append(full_anchor(db,anchor["source_key"],after_sources[anchor["source_key"]]["revision"],anchor["locator"]))
        else:
            result.append(anchor)
    return result


def migration_evidence(store, db, before, after):
    validate_semantics(before,after)
    if (set(before["sources"]) != set(after["sources"])
            or any(before["sources"][k] != after["sources"][k] for k in before["sources"] if k not in ("D26","LAW"))):
        raise ValueError("Only D26 and LAW provenance may migrate")
    comparisons = {}
    norm = lambda text: re.sub(r"\s+", "", unicodedata.normalize("NFKC",text))
    for key in ("D26","LAW"):
        old,new = before["sources"][key],after["sources"][key]
        if (key == "D26" and (new["source_id"],new["url"]) != (old["source_id"],old["url"])) or (
                key == "LAW" and (new["source_id"],new["url"]) != ("ID-AMENDMENT-JT-LEAD","https://jtgl.beijing.gov.cn/jgj/jgxx/flfg/fl/203777/index.html")):
            raise ValueError("Unexpected replacement source")
        if source_binding(store,db,new["source_id"],new["url"],new["revision"]) != new or new["business_approval"] is not True:
            raise ValueError("Migrated source must match its actual recorded review")
        store._verify(db,old["revision"],require_review=False)
        _,old_chunks = store._content(db,old["revision"])
        _,new_chunks = store._content(db,new["revision"])
        if key == "D26":
            if (len(old_chunks) != 2 or new_chunks != old_chunks[:1]
                    or old["original_sha256"] != new["original_sha256"]):
                raise ValueError("Only the intact first service measure may replace the old two paragraphs")
            comparisons[key] = {"kind":"same_original_complete_first_measure", "retained_locator":"正文段落1",
                "removed_background_locator":"正文段落2","original_sha256":new["original_sha256"]}
        else:
            if len(old_chunks) != 23 or len(new_chunks) != 23 or [(c["locator"],norm(c["text"])) for c in old_chunks] != [(c["locator"],norm(c["text"])) for c in new_chunks]:
                raise ValueError("All 23 legal clauses must match under NFKC and whitespace normalization")
            comparisons[key] = {"kind":"same_23_clauses_other_official_original", "normalization":"NFKC_then_remove_whitespace",
                "clauses":[{"locator":a["locator"],"before_text_sha256":fingerprint(a["text"].encode()),
                            "after_text_sha256":fingerprint(b["text"].encode()),"normalized_sha256":fingerprint(norm(a["text"]).encode()),
                            "literal_equal":a["text"] == b["text"]} for a,b in zip(old_chunks,new_chunks,strict=True)]}
    records = []
    for old,new in zip(before["cases"],after["cases"],strict=True):
        for anchor in old["background_source_anchors"]:
            if anchor != full_anchor(db,anchor["source_key"],anchor["revision"],anchor["locator"]):
                raise ValueError("The archived v2 anchor must remain an exact complete source span")
        anchors = expected_anchors(db,old,after["sources"])
        relevant = [] if old["proposed_outcome_after_qualified_publication"] == "stop" else [{"revision":a["revision"],"locator":a["locator"]} for a in anchors]
        if new["background_source_anchors"] != anchors or new["proposed_relevant_evidence"] != relevant:
            raise ValueError("Unexpected evidence insertion, removal or source-span change")
        records.append({"id":old["id"],"before_sha256":fingerprint(old),"after_sha256":fingerprint(new),
                        "changed_fields":sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))})
    return {"before_reference_sha256":before["content_sha256"],"after_reference_sha256":after["content_sha256"],
            "case_semantics_sha256":fingerprint(semantic_projection(after)),"sources":comparisons,"cases":records,
            "explicit_evidence_removals":[{"case_id":"HB06","source_key":"D26","locator":"正文段落2",
                "reason":"Explanatory background is outside the approved service selection. The complete first measure already states both duration branches; the unchanged reference does not assert a statutory natural-day calculation."}]}


def validate_migration(root,store,db,reference):
    if reference.get("draft_revision") == 2:
        return reference
    if reference.get("provenance_migration") != {"path":MIGRATION.as_posix()} or not sealed(reference):
        raise ValueError("Expected explicitly bound v3 migration")
    before,manifest = archive_reference(root)
    ledger = read(root / MIGRATION)
    stale = read(root / STALE_RECEIPT)
    if (not sealed(ledger) or ledger.get("kind") != "holdout_provenance_migration_not_acceptance"
            or ledger.get("base_archive_manifest_sha256") != manifest["content_sha256"]
            or ledger.get("human_review_completed") is not False or ledger.get("acceptance_authorized") is not False
            or ledger.get("reviewer") != "Codex AI"
            or ledger.get("previous_integrity_report") != {"path":STALE_RECEIPT.as_posix(),"sha256":fingerprint((root/STALE_RECEIPT).read_bytes())}
            or stale.get("draft_integrity_passed") is not False
            or sorted(k for k,v in stale["checks"].items() if not v) != sorted(["latest_originals_and_literal_anchor_spans_match",
                "actual_export_contains_both_drafts_and_preserves_empty_approvals"])
            or ledger.get("evidence") != migration_evidence(store,db,before,reference)):
        raise ValueError("Migration ledger differs from actual source or unchanged case semantics")
    return before

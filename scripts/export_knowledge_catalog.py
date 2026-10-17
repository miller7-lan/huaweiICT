"""Export a shareable retrieval catalog without originals, credentials or private paths."""

import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    sources, documents = [], []
    metadata_fields = ("title", "region", "matter", "document_type", "document_number", "published_at", "effective_from", "effective_to", "conflict",
                       "document_signed_at", "extraction_method", "scope_confirmed", "collection_role", "date_basis", "extraction_quality_notes")
    with store.db() as db:
        for row in db.execute("SELECT * FROM sites ORDER BY id"):
            config = json.loads(row["config"])
            fields = ("id", "label", "origin", "allowed_paths", "seeds", "interval_seconds", "delay_seconds", "page_kind", "discovery_format", "pdf_ocr",
                      "attachment_only", "attachment_parent_sources", "attachment_url_layout", "attachment_max_pending", "attachment_discovery")
            sources.append({**{key: config[key] for key in fields if key in config}, "enabled_at_snapshot": config["enabled"],
                "paused_reason": row["paused_reason"], "last_success": row["last_success"],
                "access_screen_actor_kind": config.get("access_review", {}).get("actor_kind"),
                "business_approval_from_this_export": False})
        for row in db.execute("SELECT r.*,d.site_id,d.url FROM documents d JOIN revisions r ON r.document_id=d.id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY d.site_id"):
            original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
            if fingerprint(original.read_bytes()) != row["sha256"]:
                raise ValueError("Archived original fingerprint mismatch")
            selected = [dict(chunk) for chunk in db.execute("SELECT locator,category,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],))]
            metadata = json.loads(row["metadata"])
            documents.append({"source_id": row["site_id"], "url": row["url"], "revision": row["id"], "state": row["state"],
                "original_sha256": row["sha256"], "original_bytes": original.stat().st_size,
                "metadata": {key: metadata[key] for key in metadata_fields if key in metadata},
                "source_fact_assessment_summary": {key: metadata["source_fact_assessment"][key] for key in (
                    "id", "reviewer_kind", "assessed_at", "current_applicability", "business_approval", "human_review_completed")}
                    if metadata.get("source_fact_assessment") else None,
                "pdf_extraction": {key: value for key, value in metadata.get("source_observation", {}).get("pdf_extraction", {}).items()
                                   if key in ("method", "algorithm", "language", "requires_visual_review", "confidence_available", "text_normalization", "rendering_scope")},
                "selected_clauses": [{"locator": chunk["locator"], "category": chunk["category"], "characters": len(chunk["text"]),
                                      "text_sha256": fingerprint(chunk["text"].encode("utf-8"))} for chunk in selected]})
    report = {"schema_version": 1, "kind": "candidate_retrieval_catalog_not_import_or_approval",
        "captured_at": datetime.now(timezone.utc).isoformat(), "counts": store.status()["counts"], "sources": sources, "documents": documents,
        "purpose": "Share source entry points, actual archive fingerprints, pending metadata, selected-clause locators and text fingerprints for GitHub review. No original bodies, SQLite data, private filesystem paths, visitor questions, account data or tokens.",
        "reacquisition": "Use the original official URLs only after a fresh access/use check. This catalog does not enable collection, restore a database or carry business approval.",
        "corpus_caution": "Archive records include mirror selections of the same act and an official service-information update signal; the record count is not a count of distinct normative policies.",
        "date_caution": "Dates may describe document-stated commencement or page publication. Source-fact summaries marked unknown/not approved do not certify current applicability; see evaluation/knowledge/source-fact-assessments.json for the bounded assessment."}
    target = ROOT / "data/knowledge/candidate-catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"catalog": str(target.relative_to(ROOT)), "sources": len(sources), "archive_records": len(documents),
                      "current_clause_locators": sum(len(record["selected_clauses"]) for record in documents), "original_bodies_exported": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

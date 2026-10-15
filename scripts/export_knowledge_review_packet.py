"""Export candidate evidence for real review; never fills policy answers or signatures."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_classify import suggest


ROOT = Path(__file__).resolve().parents[1]


def main():
    from gov_mcp.knowledge_impact import revision_impact, duplicate_candidates
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    dataset_path = ROOT / "evaluation/knowledge/pilot-business-questions.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    ai_path = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
    ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else None
    records = []
    with store.db() as db:
        for row in db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY d.site_id"):
            original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
            if fingerprint(original.read_bytes()) != row["sha256"]:
                raise ValueError("Stored original is corrupted")
            source = db.execute("SELECT * FROM sites WHERE id=?", (row["site_id"],)).fetchone()
            config = json.loads(source["config"])
            chunks = [dict(c) for c in db.execute("SELECT ordinal,locator,category,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],))]
            records.append({"revision": row["id"], "state": row["state"], "site_id": row["site_id"], "url": row["url"],
                "original_sha256": row["sha256"], "original_path": str(original.resolve()),
                "candidate_metadata": json.loads(row["metadata"]), "chunks": chunks,
                "classification_proposals": suggest(chunks, json.loads(row["metadata"])),
                "revision_impact": revision_impact(store, row["id"], include_excerpts=True),
                "access_status": {"enabled": config["enabled"], "paused_reason": source["paused_reason"],
                                  "last_success": source["last_success"], "access_review": config.get("access_review")},
                "human_review": {"reviewer": None, "reviewed_at": None, "current_applicability": None,
                    "region_and_branch_confirmed": None, "material_relationships": None,
                    "dates_resolved": None, "use_basis_confirmed": None, "decision": None, "notes": None}})
    packet = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "candidate_review_packet_not_approval",
        "scope": dataset["scope"], "records": records, "business_questions": dataset,
        "ai_reference_answers": ai,
        "possible_duplicate_groups": duplicate_candidates(store),
        "warning": "Do not derive gold answers from tested retrieval output. Source annotations and category suggestions are unapproved."}
    target = store.root / "review-packets" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, exist_ok=False)
    path = target / "candidate-review.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"created_at": packet["created_at"], "candidate_files": len(records),
        "latest_chunk_count": sum(len(record["chunks"]) for record in records),
        "question_count": len(dataset["cases"]), "human_reviewed_questions": 0,
        "ai_reference_question_count": len(ai["cases"]) if ai else 0,
        "dataset_sha256": fingerprint(dataset_path.read_bytes()), "review_packet_sha256": fingerprint(path.read_bytes()),
        "publication_policy": publication_policy(store), "published_scopes": store.status()["counts"]["heads"],
        "business_review_completed": False,
        "revision_impact_available": True, "possible_duplicate_group_count": len(packet["possible_duplicate_groups"]["groups"]),
        "source_coverage": {"national_law": "same_2011_act_two_archived_selections_new_official_mirror_monitored_dates_pending",
            "national_norms": "two_candidates_pending_date_and_applicability_review",
            "provincial_norms": "publisher_html_fourteen_clauses_archived_scanned_notice_not_acquired_later_changes_pending",
            "provincial_update_signal": "one_archived_official_service_fact_adjustment_instrument_and_current_scope_pending",
            "provincial_scan_measures": "2021_eight_page_scan_normally_archived_native_ocr_and_ai_visual_check_current_applicability_pending",
            "municipal_changes": "not_established",
            "district_guide": "archived_candidate_current_access_paused"}}
    (ROOT / "evidence/knowledge-update/review-readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"private_packet": str(path), "candidate_files": len(records), "question_count": len(dataset["cases"]),
                      "human_reviewed_questions": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

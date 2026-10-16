"""Export candidate evidence for real review; never fills policy answers or signatures."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_classify import suggest


ROOT = Path(__file__).resolve().parents[1]


def semantic_screening_for_review(reference, registry):
    path = ROOT / "evaluation/knowledge/holdout-semantic-screening.json"
    if not path.exists():
        return {"status": "missing", "assessment": None, "batch_ready_for_freeze": False}
    review = json.loads(path.read_text(encoding="utf-8"))
    if (review.get("kind") != "ai_semantic_screening_not_acceptance"
            or fingerprint({k: v for k, v in review.items() if k != "content_sha256"}) != review.get("content_sha256")
            or any(review.get(k) is not False for k in ("batch_ready_for_freeze", "independence_certified",
                                                        "business_acceptance_executed"))):
        raise ValueError("Semantic screening is damaged or claims acceptance")
    from gov_mcp.knowledge_review import question_fingerprint
    questions = [{"id": c["id"], "question_sha256": question_fingerprint(c["question"]), "query_date": c["query_date"]}
                 for c in reference["cases"]]
    screened = [{k: c[k] for k in ("id", "question_sha256", "query_date")} for c in review["cases"]]
    file_checks = {}
    for name, artifacts in (("development_files_match", review["development_sources"]),
                            ("history_artifacts_match", review.get("history_artifacts", []))):
        match = True
        for source in artifacts:
            source_path = (ROOT / source["path"]).resolve()
            if not source_path.is_relative_to(ROOT.resolve()):
                raise ValueError("Review artifact must stay inside this project")
            match &= source_path.is_file() and fingerprint(source_path.read_bytes()) == source["sha256"]
        file_checks[name] = match
    checks = {"questions_match": screened == questions and fingerprint(questions) == review["question_set_sha256"],
              "development_registry_matches": fingerprint(registry) == review["development_registry_sha256"],
              **file_checks}
    if reference.get("draft_revision", 2) >= 3:
        from holdout_provenance import MIGRATION, semantic_projection, sealed
        migration = json.loads((ROOT / MIGRATION).read_text(encoding="utf-8"))
        checks.update(reference_contents_match=fingerprint({k:v for k,v in reference.items() if k != "content_sha256"}) == review.get("reference_contents_sha256"),
                      reference_semantics_match=fingerprint(semantic_projection(reference)) == review.get("reference_semantics_sha256"),
                      provenance_migration_matches=sealed(migration) and migration["content_sha256"] == review.get("provenance_migration_sha256"))
    bound = all(checks.values())
    return {"status": "bound" if bound else "stale", "artifact_sha256": fingerprint(path.read_bytes()),
            "binding_checks": checks, "assessment": review if bound else None, "batch_ready_for_freeze": False,
            "warning": "Retired questions cannot count toward acceptance. Retained questions only continue source review. Stale judgments are withheld; no automatic freeze or acceptance."}


def holdout_draft_for_review(store, db, policy, registry):
    paths = {name: ROOT / f"evaluation/knowledge/holdout-business-{name}-draft.json"
             for name in ("reference", "questions")}
    if not any(path.exists() for path in paths.values()):
        return None
    if not all(path.exists() for path in paths.values()):
        raise ValueError("Holdout draft pair is incomplete")
    reference, questions = (json.loads(paths[name].read_text(encoding="utf-8"))
                            for name in ("reference", "questions"))
    if (reference.get("kind") != "holdout_design_draft_not_acceptance_report"
            or fingerprint({k: v for k, v in reference.items() if k != "content_sha256"}) != reference["content_sha256"]
            or questions.get("reference_draft_sha256") != reference["content_sha256"]
            or questions.get("test_execution_authorized_by_this_file") is not False
            or any(reference.get(k) is not False for k in ("independent_acceptance_ready", "independence_certified",
                                                         "human_review_completed", "ready_for_business_publication"))
            or any(reference.get(k) is not None for k in ("reference_frozen_at", "snapshot_binding", "observed_run"))):
        raise ValueError("Holdout files are inconsistent or are no longer unapproved drafts")
    expected_questions = [{k: case[k] for k in ("id", "role", "question", "query_date")}
                          for case in reference["cases"]]
    if questions.get("cases") != expected_questions:
        raise ValueError("Holdout questions differ from their reference draft")
    from holdout_provenance import validate_migration
    validate_migration(ROOT,store,db,reference)
    stale = []
    for key, binding in reference["sources"].items():
        row, _ = store._verify(db, binding["revision"], require_review=False)
        latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id "
                            "WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1",
                            (binding["source_id"], binding["url"])).fetchone()
        if (latest is None or latest[0] != row["id"] or row["sha256"] != binding["original_sha256"]
                or row["body_sha256"] != binding["body_sha256"]
                or fingerprint(row["metadata"].encode()) != binding["metadata_sha256"]):
            stale.append(key)
    screening = semantic_screening_for_review(reference, registry)
    return {"kind": "unapproved_holdout_design_for_review", "reference_draft": reference,
            "question_draft": questions,
            "semantic_screening": screening,
            "file_sha256": {name: fingerprint(path.read_bytes()) for name, path in paths.items()},
            "live_binding_checks": {"sources_match_latest": not stale,
                "development_registry_matches": fingerprint(registry) == reference["development_registry_sha256"],
                "publication_policy_file_matches": fingerprint((store.root / "publication-policy.json").read_bytes()) == reference["policy_file_sha256"],
                "attributed_review_contract": policy.get("review_contract") == "attributed_v2"},
            "stale_source_keys": stale, "automatic_approval": False,
            "warning": "Draft bindings are integrity checks only. Semantic independence, current applicability, reference freeze and business acceptance remain unapproved."}


def main():
    from gov_mcp.knowledge_impact import revision_impact, duplicate_candidates
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    dataset_path = ROOT / "evaluation/knowledge/pilot-business-questions.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    ai_path = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
    ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else None
    records = []
    with store.db() as db:
        policy = publication_policy(store)
        if policy.get("review_contract") == "attributed_v2":
            from gov_mcp.knowledge_review import development_registry
            registry = development_registry(db)
        else:
            registry = []
        holdout = holdout_draft_for_review(store, db, policy, registry)
        for row in db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY d.site_id"):
            original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
            if fingerprint(original.read_bytes()) != row["sha256"]:
                raise ValueError("Stored original is corrupted")
            source = db.execute("SELECT * FROM sites WHERE id=?", (row["site_id"],)).fetchone()
            config = json.loads(source["config"])
            chunks = [dict(c) for c in db.execute("SELECT ordinal,locator,category,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],))]
            recorded_review = None
            if row["state"] == "reviewed":
                store._verify(db, row["id"])
                review = db.execute("SELECT reviewer,digest,reviewed_at FROM reviews WHERE revision_id=?", (row["id"],)).fetchone()
                recorded_review = dict(review)
            records.append({"revision": row["id"], "state": row["state"], "site_id": row["site_id"], "url": row["url"],
                "recorded_review": recorded_review,
                "original_sha256": row["sha256"], "original_path": str(original.resolve()),
                "candidate_metadata": json.loads(row["metadata"]), "chunks": chunks,
                "classification_proposals": suggest(chunks, json.loads(row["metadata"])),
                "revision_impact": revision_impact(store, row["id"], include_excerpts=True),
                "access_status": {"enabled": config["enabled"], "paused_reason": source["paused_reason"],
                                  "last_success": source["last_success"], "access_review": config.get("access_review")},
                "human_review": {"reviewer": None, "reviewed_at": None, "current_applicability": None,
                    "region_and_branch_confirmed": None, "material_relationships": None,
                    "dates_resolved": None, "use_basis_confirmed": None, "decision": None, "notes": None},
                "attributed_review": {"actor": None, "reviewed_at": None, "current_applicability": None,
                    "region_and_branch_confirmed": None, "material_relationships": None,
                    "date_types_and_unknowns": None, "use_basis": None, "decision": None, "notes": None}})
    packet = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "candidate_review_packet_not_approval",
        "scope": dataset["scope"], "records": records, "business_questions": dataset,
        "ai_reference_answers": ai,
        "holdout_design_draft": holdout,
        "evaluation_contract": {"publication_policy": policy,
            "allowed_reference_actor_kinds": ["human", "ai"] if policy.get("review_contract") == "attributed_v2" else ["human"],
            "registered_development_question_sha256": registry, "development_registry_sha256": fingerprint(registry),
            "automatic_approval": False},
        "possible_duplicate_groups": duplicate_candidates(store),
        "warning": "Do not derive gold answers from tested retrieval output. Recorded source reviews are separate from blank review forms and unapproved classification suggestions; this packet is not a publication approval."}
    target = store.root / "review-packets" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, exist_ok=False)
    path = target / "candidate-review.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"created_at": packet["created_at"], "candidate_files": len(records),
        "latest_chunk_count": sum(len(record["chunks"]) for record in records),
        "question_count": len(dataset["cases"]), "human_reviewed_questions": 0,
        "recorded_reviewed_source_count": sum(r["recorded_review"] is not None for r in records),
        "reviewed_national_statutes": [{"source_id":r["site_id"],"revision":r["revision"],"reviewer":r["recorded_review"]["reviewer"]}
            for r in records if r["recorded_review"] and r["candidate_metadata"].get("document_type") == "statute"
            and r["candidate_metadata"].get("region") == "national"],
        "reviewed_service_information": [{"source_id":r["site_id"],"revision":r["revision"],
            "reviewer":r["recorded_review"]["reviewer"],"date_basis":r["candidate_metadata"]["date_basis"]}
            for r in records if r["recorded_review"]
            and r["candidate_metadata"].get("date_basis", {}).get("kind") == "reported_service_information"],
        "reviewed_fee_clauses": [{"source_id":r["site_id"],"revision":r["revision"],
            "reviewer":r["recorded_review"]["reviewer"],"effective_from":r["candidate_metadata"].get("effective_from"),
            "scope_summary":r["candidate_metadata"].get("source_fact_assessment", {}).get("current_applicability")}
            for r in records if r["recorded_review"] and r["candidate_metadata"].get("collection_role")
            in ("fee_candidate", "fee_first_issue_boundary", "fee_waiver_candidate")],
        "ai_reference_question_count": len(ai["cases"]) if ai else 0,
        "holdout_design_draft": {"included": holdout is not None,
            "question_count": len(holdout["question_draft"]["cases"]) if holdout else 0,
            "file_sha256": holdout["file_sha256"] if holdout else {},
            "live_binding_checks": holdout["live_binding_checks"] if holdout else {},
            "stale_source_keys": holdout["stale_source_keys"] if holdout else [],
            "semantic_screening_status": holdout["semantic_screening"]["status"] if holdout else "missing",
            "semantic_screening_counts": holdout["semantic_screening"]["assessment"]["counts"]
                if holdout and holdout["semantic_screening"]["assessment"] else {},
            "historically_retired_cases": len(holdout["semantic_screening"]["assessment"].get("historically_retired_case_ids", []))
                if holdout and holdout["semantic_screening"]["assessment"] else None,
            "independence_certified": False, "reference_frozen": False, "business_acceptance_executed": False},
        "dataset_sha256": fingerprint(dataset_path.read_bytes()), "review_packet_sha256": fingerprint(path.read_bytes()),
        "publication_policy": publication_policy(store), "published_scopes": store.status()["counts"]["heads"],
        "evaluation_review_contract": policy.get("review_contract", "legacy_human_v1"),
        "registered_development_questions": len(registry), "development_registry_sha256": fingerprint(registry),
        "business_review_completed": False,
        "revision_impact_available": True, "possible_duplicate_group_count": len(packet["possible_duplicate_groups"]["groups"]),
        "source_coverage": {"national_law": "See reviewed_national_statutes and their bound assessments for completed source reviews. Existing 2011 text, historical 2003 text, and the dated directory remain distinct; the directory cutoff is 2026-08-28, not a guarantee of later legislative status.",
            "national_norms": "two_candidates_pending_date_and_applicability_review",
            "provincial_norms": "publisher_html_and_64_page_scanned_notice_archived_identity_and_article159_checked_current_applicability_pending",
            "provincial_update_signal": "one_archived_official_service_fact_adjustment_instrument_and_current_scope_pending",
            "provincial_scan_measures": "2021_eight_page_scan_normally_archived_native_ocr_and_ai_visual_check_current_applicability_pending",
            "later_provincial_duration_signal": "See reviewed_service_information for actually reviewed date-bound service paragraphs. The explanatory background and 2024/2025 context are separate; exact commencement and individual delivery/calendar calculations remain unestablished.",
            "materials_relationships": "2023_provincial_away_reply_and_local_norm_relations_annotated_not_approved",
            "fees": "See reviewed_fee_clauses for actual scoped approvals. First issue, expiry renewal, hardship exemption/reduction and extra charges remain separate; a normally acquired 2004 hardship original is pending local-currentness/proof review. Do not infer local eligibility or extra fees from the directory citations.",
            "municipal_changes": "2026_erqi_nearby_service_official_web_observation_collector_access_denied_not_locally_archived",
            "district_guide": "archived_candidate_current_access_paused"}}
    (ROOT / "evidence/knowledge-update/review-readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"private_packet": str(path), "candidate_files": len(records), "question_count": len(dataset["cases"]),
                      "human_reviewed_questions": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

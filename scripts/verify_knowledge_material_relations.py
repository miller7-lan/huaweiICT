"""Verify the bounded AI comparison against originals, branches and a full restore.

This verifies provenance and preserved distinctions, not present legal effect or
the correctness of a local material checklist. It never approves or publishes.
"""

import json
from datetime import datetime, timezone

import httpx

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_classify import VERSION
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_review import development_registry
from nexent_api import ROOT, RUNTIME


def main():
    path = ROOT / "evaluation/knowledge/material-branch-assessments.json"
    assessment = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in assessment.items() if key != "assessment_sha256"}
    backup = json.loads((ROOT / "evidence/knowledge-update/full-backup-restore.json").read_text(encoding="utf-8"))
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    checks = {"assessment_hash_matches": fingerprint(unsigned) == assessment["assessment_sha256"],
        "truthful_ai_and_no_release_claim": assessment["reviewer_kind"] == "ai" and not assessment["human_review_completed"] and not assessment["business_release_ready"],
        "source_originals_and_latest_revisions_match": True,
        "all_unicode_spans_and_parent_hashes_match": True,
        "same_source_spans_survive_restore": True,
        "new_source_acquired_and_monitored_normally": True,
        "new_source_classification_remains_a_proposal": True,
        "source_branches_and_operators_stay_separate": True,
        "no_approval_or_publication_created": True,
        "policy_and_development_registry_preserved": True,
        "prior_model_results_and_standards_preserved": True,
        "live_read_only_service_retains_stop": True}
    with store.db() as db, restored.db() as saved:
        for source, binding in assessment["sources"].items():
            row, _ = store._verify(db, binding["revision"], require_review=False)
            archived, _ = restored._verify(saved, binding["revision"], require_review=False)
            latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (source,)).fetchone()[0]
            checks["source_originals_and_latest_revisions_match"] &= latest == binding["revision"] and row["sha256"] == binding["original_sha256"] and row["body_sha256"] == binding["body_sha256"] and row["state"] == "pending_review"
            checks["same_source_spans_survive_restore"] &= dict(row) == dict(archived)
        for record in assessment["records"]:
            checks["truthful_ai_and_no_release_claim"] &= not record["business_approval"] and record["current_erqi_applicability"] == "not_established"
            for anchor in record["anchors"]:
                current = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"], anchor["chunk_ordinal"])).fetchone()
                copy = saved.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"], anchor["chunk_ordinal"])).fetchone()
                text = current["text"]
                checks["all_unicode_spans_and_parent_hashes_match"] &= (current["locator"] == anchor["locator"] and 0 <= anchor["start"] < anchor["end"] <= len(text)
                    and fingerprint(text.encode()) == anchor["parent_text_sha256"] and fingerprint(text[anchor["start"]:anchor["end"]].encode()) == anchor["span_sha256"]
                    and anchor["revision"] == assessment["sources"][anchor["source_id"]]["revision"] and anchor["original_sha256"] == assessment["sources"][anchor["source_id"]]["original_sha256"])
                checks["same_source_spans_survive_restore"] &= tuple(current) == tuple(copy)
        by_id = {record["id"]: record for record in assessment["records"]}
        checks["source_branches_and_operators_stay_separate"] = (
            by_id["HENAN-RENEWAL"]["source_branch"] == "renewal" and by_id["HENAN-REPLACEMENT"]["source_branch"] == "replacement_after_loss"
            and by_id["HENAN-SHARING"]["relationship"] == "conditional_paper_waiver" and by_id["HENAN-RELATION-PROOF"]["source_branch"] == "household_registration"
            and by_id["ERQI-LIST"]["relationship"] == "listed_separately_operator_not_explicit"
            and by_id["REPLY-PROVINCIAL-AWAY"]["source_branch"] == "province_internal_away_from_registered_location"
            and not any(assessment["decision"][key] for key in ("local_national_provincial_requirements_reconciled", "unconditional_material_checklist_approved", "new_reply_supersedes_existing_norms")))
        source = db.execute("SELECT * FROM sites WHERE id='HN-MATERIALS-REPLY-2023'").fetchone()
        config = json.loads(source["config"])
        jobs = [dict(row) for row in db.execute("SELECT id,state,attempts,error FROM jobs WHERE site_id=?", (source["id"],))]
        new_revision = assessment["sources"][source["id"]]["revision"]
        metadata = json.loads(db.execute("SELECT metadata FROM revisions WHERE id=?", (new_revision,)).fetchone()[0])
        checks["new_source_acquired_and_monitored_normally"] = bool(jobs) and all(job["state"] == "done" and not job["error"] for job in jobs) and source["last_success"] is not None and source["paused_reason"] is None and config["enabled"] and config["delay_seconds"] >= 5 and config["paragraph_expected_count"] == 3 and config["include_paragraph_keywords"] == ["省内", "居民身份证"] and metadata["scope_confirmed"] is False and metadata["effective_from"] is None
        proposal = db.execute("SELECT suggestions,sha256 FROM classification_proposals WHERE revision_id=? AND taxonomy_version=?", (new_revision, VERSION)).fetchone()
        suggestions = json.loads(proposal["suggestions"]) if proposal else {}
        checks["new_source_classification_remains_a_proposal"] = bool(proposal) and fingerprint(suggestions) == proposal["sha256"] and suggestions["status"] == "machine_proposal_not_approved" and len(suggestions["sections"]) == 3
        approvals = {table: db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0] for table in ("reviews", "snapshot_evaluations", "versions", "heads")}
        checks["no_approval_or_publication_created"] = approvals == assessment["approval_record_counts"] == dict.fromkeys(approvals, 0)
        checks["policy_and_development_registry_preserved"] = (fingerprint((store.root / "publication-policy.json").read_bytes()) == assessment["pre_intake_policy_sha256"]
            and publication_policy(store) == publication_policy(restored) and development_registry(db) == development_registry(saved) and len(development_registry(db)) == 40)
    for name, expected in assessment["preserved_development_files"].items():
        checks["prior_model_results_and_standards_preserved"] &= fingerprint((ROOT / name).read_bytes()) == expected
    with httpx.Client(trust_env=False, timeout=10) as http:
        status = http.get("http://127.0.0.1:8772/api/status"); status.raise_for_status()
        value = http.get("http://127.0.0.1:8772/api/search", params={"q": "身份证到期换领材料", "region": "郑州市二七区", "matter": "demo-id-renewal"}); value.raise_for_status()
        result = value.json()
        checks["live_read_only_service_retains_stop"] = result["message_code"] == "NO_PUBLISHED_KNOWLEDGE" and not result["citations"] and result["knowledge_version"] is None
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "assessment_sha256": assessment["assessment_sha256"], "source_count": len(assessment["sources"]), "comparison_records": len(assessment["records"]), "anchors": assessment["anchor_count"],
        "canonical_counts": store.status()["counts"], "new_source_jobs": jobs, "business_accuracy_measured": False,
        "scope": "Source/branch provenance and restored literal spans only; no material checklist, current Erqi applicability, release approval or independent business acceptance."}
    (ROOT / "evidence/knowledge-update/material-branch-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "counts": report["canonical_counts"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

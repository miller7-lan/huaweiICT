"""Verify this bounded AI source assessment against actual live and restored archives."""

import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_review import development_registry


ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "evaluation/knowledge/source-fact-assessments.json"
    assessment = json.loads(path.read_text(encoding="utf-8"))
    content = dict(assessment)
    digest = content.pop("content_sha256")
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    backup = json.loads((ROOT / "evidence/knowledge-update/full-backup-restore.json").read_text(encoding="utf-8"))
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    pre_backup = json.loads((ROOT / "evidence/knowledge-update/source-facts-pre-amendment-backup.json").read_text(encoding="utf-8"))
    before_store = KnowledgeStore(pre_backup["restore"]["restored_to"], read_only=True)
    checks = {"assessment_fingerprint_matches": fingerprint(content) == digest,
        "three_sources_and_twelve_facts": len(assessment["records"]) == 3 and assessment["fact_count"] == 12,
        "truthful_ai_pending_status": assessment["reviewer_kind"] == "ai" and assessment["human_review_completed"] is False
            and assessment["business_release_ready"] is False,
        "original_and_clause_fingerprints_unchanged": True, "only_declared_metadata_fields_changed": True,
        "all_literal_source_anchors_match": True, "date_types_and_values_match_anchors": True,
        "latest_candidates_are_pending": True, "all_assessments_remain_source_facts_only": True,
        "restored_assessments_match_live": True, "reference_dataset_bound_to_current_sources": True,
        "source_access_reviews_and_limits_unchanged": True,
        "material_comparisons_retain_both_sources": True,
        "no_formal_approval_records_created": True, "current_policy_and_development_registry_preserved": True}
    facts = 0
    with store.db() as db, restored.db() as saved, before_store.db() as prior:
        for record in assessment["records"]:
            source = db.execute("SELECT config,paused_reason FROM sites WHERE id=?", (record["site_id"],)).fetchone()
            old_source = prior.execute("SELECT config,paused_reason FROM sites WHERE id=?", (record["site_id"],)).fetchone()
            checks["source_access_reviews_and_limits_unchanged"] &= (json.loads(source["config"]) == json.loads(old_source["config"])
                and source["paused_reason"] == old_source["paused_reason"])
            base, _ = store._verify(db, record["base_revision"], require_review=False)
            row, _ = store._verify(db, record["new_revision"], require_review=False)
            old, current = json.loads(base["metadata"]), json.loads(row["metadata"])
            _, clauses = store._content(db, record["base_revision"])
            by_locator = {clause["locator"]: clause["text"] for clause in clauses}
            latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (record["site_id"],)).fetchone()[0]
            checks["latest_candidates_are_pending"] &= latest == row["id"] and row["state"] == "pending_review"
            checks["original_and_clause_fingerprints_unchanged"] &= (
                base["sha256"] == row["sha256"] == record["original_sha256"]
                and base["body_sha256"] == row["body_sha256"] == record["body_sha256"])
            reconstructed = copy.deepcopy(old)
            for key, change in record["field_changes"].items():
                checks["only_declared_metadata_fields_changed"] &= key in ("effective_from", "published_at", "usage_basis") and old.get(key) == change["before"]
                reconstructed[key] = change["after"]
            reconstructed["source_fact_assessment"] = record["assessment"]
            checks["only_declared_metadata_fields_changed"] &= reconstructed == current and fingerprint(old) == record["before_metadata_sha256"] and fingerprint(current) == record["new_metadata_sha256"]
            for fact in record["assessment"]["facts"]:
                facts += 1
                evidence = fact["evidence"]
                checks["all_literal_source_anchors_match"] &= evidence["revision"] == base["id"] and evidence["original_sha256"] == base["sha256"]
                if "locator" in evidence:
                    text = by_locator[evidence["locator"]]
                    checks["all_literal_source_anchors_match"] &= text[evidence["start"]:evidence["end"]] == evidence["quote"] and fingerprint(evidence["quote"].encode()) == evidence["quote_sha256"]
                else:
                    match = re.fullmatch(r"fields\.(.+)\[(\d+)\]", evidence["observation_field"])
                    text = old["source_observation"]["fields"][match[1]][int(match[2])]
                    checks["all_literal_source_anchors_match"] &= evidence["quote"] in text and fingerprint(text) == evidence["observation_sha256"]
                if fact.get("date_kind") == "document_stated_commencement":
                    value = date.fromisoformat(fact["value"])
                    checks["date_types_and_values_match_anchors"] &= current["effective_from"] == fact["value"] and f"{value.year}年{value.month}月{value.day}日" in evidence["quote"]
                elif fact.get("date_kind") == "page_declared_publication":
                    checks["date_types_and_values_match_anchors"] &= current["published_at"] == fact["value"] and fact["value"] in evidence["quote"]
                checks["all_assessments_remain_source_facts_only"] &= fact["current_applicability"] == "unknown"
            annotation = current["source_fact_assessment"]
            checks["all_assessments_remain_source_facts_only"] &= annotation["business_approval"] is False and annotation["human_review_completed"] is False and current["business_review"] == "pending"
            saved_row, _ = restored._verify(saved, row["id"], require_review=False)
            checks["restored_assessments_match_live"] &= json.loads(saved_row["metadata"]) == current
        approval = {table: db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0] for table in ("reviews", "snapshot_evaluations", "versions", "heads")}
        checks["no_formal_approval_records_created"] = approval == assessment["approval_record_counts_before"] == assessment["approval_record_counts_after"]
        checks["current_policy_and_development_registry_preserved"] = publication_policy(store) == publication_policy(restored) and development_registry(db) == development_registry(saved)
        reference_path = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        checks["reference_dataset_bound_to_current_sources"] &= reference["source_fact_assessment_sha256"] == fingerprint(path.read_bytes()) and reference["publication_policy_sha256"] == fingerprint(publication_policy(store))
        for source, ref in reference["archived_sources"].items():
            latest = db.execute("SELECT r.id,r.sha256 FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (source,)).fetchone()
            checks["reference_dataset_bound_to_current_sources"] &= latest["id"] == ref["revision"] and latest["sha256"] == ref["sha256"]
        for case in reference["cases"]:
            if case["id"] in ("KQ01", "KQ02", "KQ09", "KQ19"):
                checks["material_comparisons_retain_both_sources"] &= {"MPS-NORM-01", "HN-NORM-PUBLISHER"} <= set(case["archived_evidence_ids"])
    checks["three_sources_and_twelve_facts"] &= facts == 12
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "checks": checks, "passed": all(checks.values()),
        "source_fact_assessment_sha256": fingerprint(path.read_bytes()), "reference_sha256": fingerprint(reference_path.read_bytes()),
        "sources": len(assessment["records"]), "facts": facts, "counts": store.status()["counts"],
        "model_requests": 0, "human_review_completed": False, "business_release_ready": False,
        "scope": "Literal source/date/metadata, current AI draft bindings and real backup integrity for three pending amendments only. No current applicability, material obligation, semantic independence or policy-answer acceptance approval."}
    (ROOT / "evidence/knowledge-update/source-fact-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "sources": report["sources"], "facts": facts, "checks": checks}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

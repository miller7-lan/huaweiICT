"""Check the fee/norm identity evidence and restored original selections, read-only."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_review import development_registry
from nexent_api import ROOT, RUNTIME
from record_fee_norm_evidence import SPECS, selected_chunks


def main():
    parser = argparse.ArgumentParser(description="核对收费依据链和规范原件身份，不批准发布")
    parser.add_argument("--research-directory", required=True)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    research = Path(args.research_directory).resolve()
    assessment = json.loads((ROOT / "evaluation/knowledge/fee-norm-identity-assessments.json").read_text(encoding="utf-8"))
    backup = json.loads((ROOT / "evidence/knowledge-update/full-backup-restore.json").read_text(encoding="utf-8"))
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    checks = {"assessment_hash_matches": fingerprint({k:v for k,v in assessment.items() if k != "assessment_sha256"}) == assessment["assessment_sha256"],
        "truthful_ai_review_and_no_release_claim": assessment["reviewer_kind"] == "ai" and not assessment["human_review_completed"] and not assessment["business_release_ready"],
        "latest_revisions_and_originals_match": True, "original_selections_reproduce": True,
        "all_anchors_reproduce_and_survive_restore": True, "references_disabled_and_excluded": True,
        "both_primary_policies_acquired_and_monitored_normally": True,
        "dates_and_fee_branch_boundaries_preserved": True, "pdf_visual_observation_matches_cached_images": True,
        "current_validity_not_invented": True, "policy_registry_and_prior_model_records_preserved": True,
        "no_approvals_or_published_versions_created": True, "live_unpublished_query_retains_stop": True}
    with store.db() as db, restored.db() as saved:
        for source_id, binding in assessment["sources"].items():
            row, _ = store._verify(db, binding["revision"], require_review=False)
            copy, _ = restored._verify(saved, binding["revision"], require_review=False)
            latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (source_id,)).fetchone()[0]
            checks["latest_revisions_and_originals_match"] &= latest == row["id"] and row["sha256"] == binding["original_sha256"] and row["state"] == binding["state"] and dict(row) == dict(copy)
            source = db.execute("SELECT * FROM sites WHERE id=?", (source_id,)).fetchone()
            config = json.loads(source["config"])
            meta = json.loads(row["metadata"])
            if source_id in SPECS:
                original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
                expected = selected_chunks(source_id, original)
                _, actual = store._content(db, row["id"])
                _, recovered = restored._content(saved, row["id"])
                checks["original_selections_reproduce"] &= expected == actual == recovered
                checks["references_disabled_and_excluded"] &= not config["enabled"] and config["metadata"]["collection_role"] == "governance_reference" and row["state"] == "excluded"
                store._verify_exclusion(db, row["id"])
                checks["current_validity_not_invented"] &= meta["effective_from"] is None and meta["effective_to"] is None and not meta["scope_confirmed"]
            if source_id.startswith("MOF-"):
                jobs = list(db.execute("SELECT state,error FROM jobs WHERE site_id=?", (source_id,)))
                checks["both_primary_policies_acquired_and_monitored_normally"] &= (config["enabled"] and config["interval_seconds"] == 86400 and config["delay_seconds"] >= 5
                    and source["last_success"] is not None and not source["paused_reason"] and bool(jobs)
                    and all(r["state"] == "done" and not r["error"] for r in jobs)
                    and meta["source_observation"]["media_type"] == "text/html" and not meta["scope_confirmed"]
                    and config.get("metadata_selectors") == {"displayed_publication": {"class": "laiyuan"}})
        for finding in assessment["findings"]:
            checks["truthful_ai_review_and_no_release_claim"] &= not finding["business_approval"]
            for anchor in finding["anchors"]:
                chunk = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"], anchor["chunk_ordinal"])).fetchone()
                copy = saved.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"], anchor["chunk_ordinal"])).fetchone()
                text = chunk["text"]
                checks["all_anchors_reproduce_and_survive_restore"] &= (anchor["revision"] == assessment["sources"][anchor["source_id"]]["revision"]
                    and tuple(chunk) == tuple(copy) and chunk["locator"] == anchor["locator"] and 0 <= anchor["start"] < anchor["end"] <= len(text)
                    and fingerprint(text.encode()) == anchor["text_sha256"] and fingerprint(text[anchor["start"]:anchor["end"]].encode()) == anchor["span_sha256"])
        fee_id = assessment["sources"]["MOF-FEE-2007"]["revision"]
        first_id = assessment["sources"]["MOF-FIRST-ISSUE-2018"]["revision"]
        fee_meta = json.loads(db.execute("SELECT metadata FROM revisions WHERE id=?", (fee_id,)).fetchone()[0])
        first_meta = json.loads(db.execute("SELECT metadata FROM revisions WHERE id=?", (first_id,)).fetchone()[0])
        first = db.execute("SELECT text FROM chunks WHERE revision_id=?", (first_id,)).fetchone()[0]
        fee = db.execute("SELECT text FROM chunks WHERE revision_id=? AND ordinal=1", (fee_id,)).fetchone()[0]
        checks["dates_and_fee_branch_boundaries_preserved"] = (fee_meta["document_signed_at"] == "2007-02-14" and first_meta["document_signed_at"] == "2018-04-12"
            and fee_meta["effective_from"] is None and first_meta["effective_from"] is None
            and fee_meta["initial_page_observations"]["page_date"] == "2008-05-19" and first_meta["initial_page_observations"]["page_date"] == "2018-08-20"
            and fee_meta["source_observation"]["fields"]["meta:pubdate"] == ["2007-09-29 08:17:00"]
            and first_meta["source_observation"]["fields"]["meta:pubdate"] == ["2018-04-16 17:37:00"]
            and any("2008年05月19日" in t for t in fee_meta["source_observation"]["fields"]["block:displayed_publication"])
            and any("2018年08月20日" in t for t in first_meta["source_observation"]["fields"]["block:displayed_publication"])
            and first == "一、自2018年4月1日起，停征首次申领居民身份证工本费。"
            and all(t in fee for t in ("换领第二代居民身份证", "每证20元", "遗失补领、损坏换领", "每证40元", "临时居民身份证", "每证10元")))
        for n, sha in assessment["norm_pdf"]["rendered_page_sha256"].items():
            checks["pdf_visual_observation_matches_cached_images"] &= fingerprint((research / f"norm-page-{n}.png").read_bytes()) == sha
        checks["current_validity_not_invented"] &= (assessment["norm_pdf"]["pages_visually_inspected"] == [1,45,64]
            and assessment["current_erqi_applicability"] == "not_established" and len(assessment["remaining"]) == 4)
        checks["policy_registry_and_prior_model_records_preserved"] = (fingerprint((store.root / "publication-policy.json").read_bytes()) == assessment["policy_file_sha256"]
            and development_registry(db) == development_registry(saved) and len(development_registry(db)) == 40)
        checks["no_approvals_or_published_versions_created"] = all(db.execute("SELECT COUNT(*) FROM "+name).fetchone()[0] == 0 for name in ("reviews", "snapshot_evaluations", "versions", "heads"))
    for name, expected in assessment["preserved_development_files"].items():
        checks["policy_registry_and_prior_model_records_preserved"] &= fingerprint((ROOT / name).read_bytes()) == expected
    with httpx.Client(trust_env=False, timeout=10) as http:
        response = http.get("http://127.0.0.1:8772/api/search", params={"q":"身份证到期换领费用", "region":"郑州市二七区", "matter":"demo-id-renewal"})
        response.raise_for_status()
        answer = response.json()
        checks["live_unpublished_query_retains_stop"] = answer["message_code"] == "NO_PUBLISHED_KNOWLEDGE" and not answer["citations"] and answer["knowledge_version"] is None
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "assessment_sha256": assessment["assessment_sha256"], "sources": len(assessment["sources"]), "new_originals": 5,
        "findings": len(assessment["findings"]), "anchors": sum(len(f["anchors"]) for f in assessment["findings"]),
        "counts": store.status()["counts"], "business_accuracy_measured": False, "model_trials_started": 0,
        "scope": "Original identity, exact selected cells/pages/paragraphs, source chain and restoration. No current Erqi fee/material approval or independent business acceptance."}
    (ROOT / "evidence/knowledge-update/fee-norm-identity-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "counts": report["counts"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Check actual publisher discovery, dated source relations and restored evidence."""

import json
from datetime import datetime, timezone

import httpx

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import parse_html, select_paragraph_keywords
from gov_mcp.knowledge_review import development_registry
from nexent_api import ROOT, RUNTIME
from record_duration_update_assessment import SOURCES, EXCLUSIONS


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    assessment = json.loads((ROOT / "evaluation/knowledge/duration-update-assessments.json").read_text(encoding="utf-8"))
    supplement = json.loads((ROOT / "evaluation/knowledge/duration-reference-supplement.json").read_text(encoding="utf-8"))
    backup = json.loads((ROOT / "evidence/knowledge-update/full-backup-restore.json").read_text(encoding="utf-8"))
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    checks = {"assessment_and_supplement_hashes_match": True, "latest_source_originals_and_restore_match": True,
        "all_literal_anchors_match": True, "six_hour_directory_and_daily_owner_use_normal_jobs": True,
        "two_index_pages_and_explicit_owner_deduplication": True, "deployed_background_worker_processed_owner": True,
        "2026_selection_and_three_date_layers_preserved": True,
        "scope_exclusions_are_verified_and_not_repeal_claims": True, "ai_and_development_status_truthful": True,
        "old_development_references_and_model_results_preserved": True, "publication_policy_and_registry_preserved": True,
        "no_approvals_or_published_versions_created": True, "live_read_only_service_retains_stop": True}
    for value, key in ((assessment,"assessment_sha256"), (supplement,"content_sha256")):
        checks["assessment_and_supplement_hashes_match"] &= fingerprint({k:v for k,v in value.items() if k != key}) == value[key]
    with store.db() as db, restored.db() as saved:
        for key, (source_id, url) in SOURCES.items():
            binding = assessment["sources"][key]
            row, _ = store._verify(db, binding["revision"], require_review=False)
            copy, _ = restored._verify(saved, binding["revision"], require_review=False)
            latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (source_id,url)).fetchone()[0]
            checks["latest_source_originals_and_restore_match"] &= latest == row["id"] and row["sha256"] == binding["original_sha256"] and row["body_sha256"] == binding["body_sha256"] and dict(row) == dict(copy)
            if key in EXCLUSIONS:
                store._verify_exclusion(db, row["id"])
                restored._verify_exclusion(saved, row["id"])
                reason = db.execute("SELECT reason FROM exclusions WHERE revision_id=?", (row["id"],)).fetchone()[0]
                checks["scope_exclusions_are_verified_and_not_repeal_claims"] &= reason == EXCLUSIONS[key]
        for finding in assessment["findings"]:
            checks["ai_and_development_status_truthful"] &= finding["business_release_approval"] is False
            for anchor in finding["anchors"]:
                row = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"],anchor["ordinal"])).fetchone()
                copy = saved.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (anchor["revision"],anchor["ordinal"])).fetchone()
                text = row["text"]
                checks["all_literal_anchors_match"] &= (anchor["revision"] == assessment["sources"][anchor["source_key"]]["revision"] and tuple(row) == tuple(copy)
                    and row["locator"] == anchor["locator"] and 0 <= anchor["start"] < anchor["end"] <= len(text)
                    and fingerprint(text.encode()) == anchor["parent_text_sha256"] and fingerprint(text[anchor["start"]:anchor["end"]].encode()) == anchor["span_sha256"])
        index = db.execute("SELECT * FROM sites WHERE id='HN-INTERPRETATION-DISCOVERY'").fetchone()
        owner = db.execute("SELECT * FROM sites WHERE id='HN-MEASURES-2026'").fetchone()
        ic, oc = json.loads(index["config"]), json.loads(owner["config"])
        jobs = [dict(r) for r in db.execute("SELECT id,site_id,state,error,owner FROM jobs WHERE site_id IN ('HN-INTERPRETATION-DISCOVERY','HN-MEASURES-2026')")]
        checks["six_hour_directory_and_daily_owner_use_normal_jobs"] = (ic["enabled"] and oc["enabled"] and ic["interval_seconds"] == 21600 and oc["interval_seconds"] == 86400
            and index["last_success"] is not None and owner["last_success"] is not None and not index["paused_reason"] and not owner["paused_reason"]
            and len(jobs) == 8 and all(j["state"] == "done" and not j["error"] for j in jobs))
        cycle = db.execute("SELECT MAX(id) FROM discovery_cycles WHERE site_id=?", (index["id"],)).fetchone()[0]
        visits = [dict(r) for r in db.execute("SELECT url,kind FROM discovery_visits WHERE cycle_id=?", (cycle,))]
        checks["two_index_pages_and_explicit_owner_deduplication"] = (sum(v["kind"] == "index" for v in visits) == 2 and ic["discovery_max_pages"] == 3 and ic["discovery_max_links"] == 10
            and ic["discovery_document_owners"] == {SOURCES["HN2026"][1]: "HN-MEASURES-2026"}
            and db.execute("SELECT COUNT(*) FROM documents WHERE url=?", (SOURCES["HN2026"][1],)).fetchone()[0] == 1)
        binding = assessment["sources"]["HN2026"]
        row, chunks = store._content(db, binding["revision"])
        metadata = json.loads(row["metadata"])
        original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
        parsed,_ = parse_html(original.read_bytes(),oc["content_selector"])
        selected = select_paragraph_keywords(parsed,oc["include_paragraph_keywords"],oc["paragraph_expected_count"])
        fields = metadata["source_observation"]["fields"]
        checks["deployed_background_worker_processed_owner"] = any(j["site_id"] == "HN-MEASURES-2026" and j["owner"] == "knowledge-worker" and j["state"] == "done" for j in jobs) and "meta:lastmodifiedtime" in fields
        checks["2026_selection_and_three_date_layers_preserved"] = (selected == chunks and len(chunks) == 2
            and fields["block:page_date"] == ["2026-05-05"] and fields["meta:lastmodifiedtime"] == ["2026-05-11 09:23"]
            and metadata["effective_from"] is None and metadata["published_at"] is None and not metadata["scope_confirmed"]
            and "从受理之日起到领证之日" in chunks[0]["text"] and "本省户籍居民" in chunks[0]["text"]
            and "中考、高考特殊时期急需用证考生" in chunks[0]["text"] and "5个工作日" in chunks[0]["text"])
        checks["no_approvals_or_published_versions_created"] = all(db.execute("SELECT COUNT(*) FROM "+name).fetchone()[0] == 0 for name in ("reviews","snapshot_evaluations","versions","heads"))
        checks["publication_policy_and_registry_preserved"] = fingerprint((store.root/"publication-policy.json").read_bytes()) == assessment["policy_file_sha256"] and development_registry(db) == development_registry(saved) and len(development_registry(db)) == 40
    for name, expected in assessment["preserved_development_files"].items():
        checks["old_development_references_and_model_results_preserved"] &= fingerprint((ROOT/name).read_bytes()) == expected
    checks["ai_and_development_status_truthful"] &= (assessment["reviewer_kind"] == supplement["author_kind"] == "ai" and not assessment["human_review_completed"]
        and not assessment["business_release_ready"] and not supplement["ready_for_business_publication"]
        and supplement["dataset_role"] == "development_only_not_independent_acceptance" and supplement["case_updates"][0]["id"] == "KQ13"
        and fingerprint((ROOT/supplement["base_reference"]).read_bytes()) == supplement["base_file_sha256"] and supplement["source_assessment_sha256"] == assessment["assessment_sha256"])
    with httpx.Client(trust_env=False,timeout=10) as http:
        status = http.get("http://127.0.0.1:8772/api/status"); status.raise_for_status()
        response = http.get("http://127.0.0.1:8772/api/search",params={"q":"身份证到期换领办理时限", "region":"郑州市二七区", "matter":"demo-id-renewal"});response.raise_for_status();answer=response.json()
        checks["live_read_only_service_retains_stop"] = status.json()["mode"] == "knowledge_update" and answer["message_code"] == "NO_PUBLISHED_KNOWLEDGE" and not answer["citations"]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "assessment_sha256": assessment["assessment_sha256"], "sources": len(SOURCES), "findings": len(assessment["findings"]),
        "anchors": sum(len(f["anchors"]) for f in assessment["findings"]), "counts": store.status()["counts"], "model_generations": 0,
        "scope": "Actual source discovery, owner reuse, exact selected clauses/date observations and restored AI finding spans. No six-hour elapsed-period proof, current legal-effect certification or independent business acceptance."}
    (ROOT/"evidence/knowledge-update/duration-update-integrity.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":report["passed"], "checks":checks, "counts":report["counts"]},ensure_ascii=False))
    if not report["passed"]:raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Verify a municipal web observation without treating it as an archived original."""
import json
import re
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    private = store.root/"source-research/zhengzhou-consent-adjustment-20261018"
    report = read(ROOT/"evaluation/knowledge/zhengzhou-undertaking-adjustment-observation-20261018.json")
    before = read(private/"before-observation.json")
    prior = read(ROOT/"evaluation/knowledge/zhengzhou-household-undertaking-facts-20261017.json")
    source, comparison, attempt = report["source"], report["archived_2021_comparison"], report["normal_fetch_attempt"]
    checks = {
        "observation_digest_matches":fingerprint({k:v for k,v in report.items() if k != "content_sha256"}) == report["content_sha256"],
        "page_and_signing_dates_not_invented_as_commencement":source["page_displayed_at"] == "2022-07-01 09:33" and source["body_signed_at"] == "2022-07-01" and source["legal_effective_from"] is None and source["first_publication_verified"] is False,
        "robots_denial_binds_exact_new_entry_and_no_body":attempt == read(private/"normal-fetch-receipt.json") and attempt["url"] == source["url"] and attempt["stop_detail"] == {"phase":"robots", "http_status":403} and attempt["body_acquired"] is False and not (private/"announcement.html").exists(),
        "web_observation_has_no_original_claim":source["original_sha256"] is None and source["body_normally_acquired"] is False and report["kind"] == "official_web_observation_not_archived_candidate_or_current_business_approval",
        "unread_attachment_and_rows_not_certified":all(report["attachment"][key] is False for key in ["file_obtained", "attachment_url_verified", "rows_read", "format_or_macros_inspected"]),
        "only_separate_bounded_observations": [f["id"] for f in report["observed_facts"]] == ["municipal_adjustment_context", "away_condition_proof_cancelled", "renewal_replacement_undertaking_retained"] and all(f["inference_limit"] for f in report["observed_facts"]),
        "business_conclusions_stay_unapproved":all(value is False for key,value in report["conclusions"].items() if key != "new_local_evidence_signal"),
        "drafts_and_scope_bytes_unchanged":all(fingerprint((ROOT/path).read_bytes()) == sha for path,sha in before["file_sha256"].items()),
    }
    with store.db() as db:
        row,_ = store._verify(db, comparison["revision"], require_review=False)
        _,chunks = store._content(db, row["id"])
        anchors = [{"locator":c["locator"], "text_sha256":fingerprint(c["text"].encode()), "characters":len(c["text"])} for c in chunks]
        checks["2021_archive_original_and_all_literal_anchors_bound"] = comparison["revision"] == prior["revision"] and comparison["source_id"] == prior["source_id"] and comparison["original_sha256"] == row["sha256"] == prior["original_sha256"] and comparison["literal_anchors"] == anchors
        row10 = re.sub(r"\s+", "", next(c["text"] for c in chunks if "第10项" in c["locator"]))
        checks["2021_row_10_proof_and_verification_remain_distinct"] = all(term in row10 for term in ["10", "郑州市公安局", "换领、补领居民身份证", "居民户口簿", "现场核查"]) and comparison["adjacent_row_11_is_distinct"] is True
        configs = {r["id"]:fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")}
        checks["source_configs_and_paused_sources_unchanged"] = configs == before["configs"]
        checks["no_new_review_evaluation_version_or_publication"] = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]} == before["gates"]
        checks["observation_not_inserted_into_candidate_corpus"] = db.execute("SELECT count(*) FROM documents WHERE url=?", (source["url"],)).fetchone()[0] == 0 and store.status()["counts"] == before["counts"]
    result = {"schema":1, "captured_at":datetime.now(timezone.utc).isoformat(), "passed":all(checks.values()), "checks":checks,
        "normal_fetch_attempts":1, "body_acquired":False, "new_archived_sources":0, "new_observed_local_adjustment":1,
        "new_model_requests":0, "candidate_publications":0, "counts":store.status()["counts"],
        "limits":"Checks prove provenance and preservation only. The web observation does not certify attachment cells, later currentness or Erqi business requirements."}
    (ROOT/"evidence/knowledge-update/municipal-adjustment-observation-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

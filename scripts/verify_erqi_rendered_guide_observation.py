"""Verify a saved public rendered guide separately from an HTTP shell or approved corpus."""
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    private = store.root / "source-research/erqi-portal-guide-20261018"
    before = read(private / "before-main.json")
    if store.status()["counts"] != before["counts"]:
        raise SystemExit("主库已有后续更新；保留最初网页观察阶段回执，不用当前状态覆盖")
    report = read(ROOT / "evaluation/knowledge/erqi-rendered-guide-observation-20261018.json")
    raw = (private / "guide-dom-snapshot.txt").read_bytes()
    text = raw.decode("utf-8")
    receipt = read(private / "normal-fetch-receipt.json")
    response = (private / "normal-response.html").read_bytes()
    materials = text.split("  - generic: 申请材料\n", 1)[1].split("  - generic: 收费信息\n", 1)[0]
    row = next(line.strip()[7:-2] for line in materials.splitlines() if line.strip().startswith('- row "1 '))
    source = report["source"]
    checks = {
        "report_digest_matches": fingerprint({k: v for k, v in report.items() if k != "content_sha256"}) == report["content_sha256"],
        "rendered_capture_digest_and_anchors_match": fingerprint(raw) == report["rendered_capture"]["sha256"] and len(raw) == report["rendered_capture"]["bytes"] and all(anchor["text"] in text and fingerprint(anchor["text"].encode()) == anchor["text_sha256"] for anchor in report["anchors"]),
        "exact_region_authority_local_variant_bound": source["url"] == receipt["url"] and source["url"].endswith("9BA34BC110B96284D26CE82456A51500?region=410103000000") and 'generic "郑州市二七区"' in text and 'generic: 申领居民身份证（换领）' in text and 'cell "郑州市公安局二七分局"' in text and source["away_variant_selected"] is False,
        "single_material_row_reproduced_without_unconditional_waiver": report["material_table"]["rendered_row"] == row and sum(line.strip().startswith('- row "1 ') for line in materials.splitlines()) == 1 and '居民户口簿' not in materials and report["conclusions"]["household_book_unconditionally_waived"] is False,
        "normal_http_shell_not_rendered_guide_original": receipt == report["normal_fetch_attempt"] and receipt["status"] == 200 and fingerprint(response) == receipt["sha256"] == "6da53882860f1e469292f932ec3f4ddbeeba2c4df964581411c527d628192eb1" and not any(receipt[k] for k in ["contains_guide_title", "contains_implementation_authority", "contains_material_row"]) and source["guide_body_normally_acquired"] is False and source["guide_original_sha256"] is None,
        "date_download_screenshot_and_popovers_not_invented": source["publication_date"] is None and source["update_date"] is None and all(report["unconfirmed"][k] is False for k in ["guide_download_obtained", "screenshot_saved", "material_popovers_read", "sample_doc_obtained"]),
        "conflicts_and_branch_limits_retained": {r["id"] for r in report["limitations"]} == {"household_book_relationship", "statutory_days", "fee_exemption", "online_and_service_time", "away_faq", "template_quality"} and all(r["unresolved"] for r in report["limitations"]) and all(v is False for v in report["conclusions"].values()),
        "draft_and_scope_files_unchanged": all(fingerprint((ROOT / path).read_bytes()) == sha for path, sha in before["file_sha256"].items()),
        "main_corpus_counts_unchanged": store.status()["counts"] == before["counts"],
    }
    with store.db() as db:
        checks["source_configs_and_gate_rows_unchanged"] = {r["id"]: fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")} == before["configs"] and {t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in before["gates"]} == before["gates"]
        checks["observation_not_inserted_in_candidate_corpus"] = db.execute("SELECT count(*) FROM documents WHERE url=?", (source["url"],)).fetchone()[0] == 0
    result = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks, "counts": store.status()["counts"], "new_rendered_guide_observations": 1, "new_normally_archived_guide_originals": 0, "business_approvals": 0, "business_publications": 0, "limits": "Local saved-capture provenance and preservation checks only; no policy currentness, unconditional material waiver or independent business acceptance certified."}
    (ROOT / "evidence/knowledge-update/erqi-rendered-guide-observation-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

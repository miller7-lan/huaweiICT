"""Select relevant complete clauses from the guarded provincial publisher HTML."""

import argparse
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import observe_html, parse_html, schedule, select_articles, worker_once
from nexent_api import ROOT, RUNTIME


SOURCE = "HN-NORM-PUBLISHER"
URL = "https://hnga.henan.gov.cn/2022/08-16/2561597.html"
ARTICLES = ["第一条", "第七条", "第九条", "第一百五十五条", "第一百五十七条", "第一百五十九条", "第一百六十一条",
            "第一百六十二条", "第一百六十三条", "第一百六十四条", "第一百六十五条", "第二百三十四条", "第二百三十五条", "第二百三十六条"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true", help="Requires the updated nested-exclusion worker")
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    intake = json.loads((ROOT / "evidence/knowledge-update/henan-norm-publisher-intake.json").read_text(encoding="utf-8"))
    raw = (store.root / "intake-2026-10-16/henan-norm-publisher.html").read_bytes()
    if not intake["body_acquired"] or fingerprint(raw) != intake["raw_sha256"] or intake["url"] != URL:
        raise ValueError("Verified guarded provincial archive required")
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (SOURCE,)).fetchone()[0])
    if config["seeds"] != [URL] or config["origin"] != "https://hnga.henan.gov.cn":
        raise ValueError("Unexpected provincial source boundary")
    config.update(content_selector={"id": "content"}, excluded_content_selectors=[{"class": "operator"}],
        metadata_selectors={"page_date": {"id": "pubDate"}}, include_articles=ARTICLES,
        article_categories={name: "eligibility" if name in ("第一百五十五条", "第一百五十七条") else
                            "household" if name == "第七条" else "identity" if name in ("第一百五十九条", "第一百六十一条", "第一百六十四条", "第一百六十五条") else "procedure" for name in ARTICLES},
        watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}], enabled=args.enable_monitoring)
    config["label"] = "河南省户口居民身份证管理规范：省公安厅发布HTML正文"
    config["metadata"].update(title="河南省公安机关户口居民身份证管理工作规范（试行）（省厅HTML正文）",
        document_type="provincial_normative_document", scope_confirmed=True,
        selected_content_scope="Fourteen complete relevant clauses, including general scope, paper-material waiver, application branches, collection and dates. Editor credit excluded; no inference that adjacent branches are mandatory pilot materials.",
        discovered_from="https://fcglzx.nanyang.gov.cn/2025/04-25/1035142.html",
        initial_page_observations={"page_date": "2022-08-16", "implementation_date_in_article236": "2022-09-01",
            "old_renewal_window_article157": "到期前三个月", "amendment_or_service_update_requires_check": True,
            "notice_signature_and_document_number_in_this_html": "not_established",
            "note": "The provincial publisher HTML supplies the normative text. It is not the signed/scanned notice; a separate 2025 official service update reports six months. Preserve the discrepancy and check later changes."})
    config["access_review"]["purpose"] = "bounded_daily_official_norm_monitoring" if args.enable_monitoring else "guarded_private_clause_selection"
    paragraphs, _ = parse_html(raw, config["content_selector"], min_text_length=1,
                               excluded_selectors=config["excluded_content_selectors"])
    selected = select_articles(paragraphs, ARTICLES, config["article_categories"])
    if len(selected) != 14 or "2022年9月1日" not in selected[-1]["text"] or any("责任编辑" in chunk["text"] for chunk in selected):
        raise ValueError("Expected complete normative selection missing or contaminated")
    observation = observe_html(raw, config["metadata_selectors"])
    metadata = dict(config["metadata"], effective_from=None, effective_to=None, published_at=None, source_observation=observation)
    store.register(config)
    revision = store.ingest(SOURCE, URL, raw, metadata, selected, preserve_metadata_if_unchanged=True)
    store.propose_classification(revision)
    if args.enable_monitoring:
        schedule(store)
        worker_once(store, owner="henan-norm-intake")
    with store.db() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
        jobs = [dict(row) for row in db.execute("SELECT id,url,state,attempts,owner,error FROM jobs WHERE site_id=?", (SOURCE,))]
        state = dict(db.execute("SELECT last_success,paused_reason,failures FROM sites WHERE id=?", (SOURCE,)).fetchone())
        if not any(job["state"] == "running" and job["owner"] == "henan-norm-intake" for job in jobs):
            db.execute("UPDATE workers SET progress='completed' WHERE id='henan-norm-intake'")
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE, "url": URL,
        "revision": revision, "original_sha256": fingerprint(raw), "selected_article_count": len(selected),
        "locators": [chunk["locator"] for chunk in selected], "editor_credit_in_selected_text": False,
        "automatic_monitoring_enabled": args.enable_monitoring, "jobs": jobs, "source_state": state,
        "counts": store.status()["counts"], "business_review_completed": False, "official_knowledge_published": 0,
        "scope": "Actual provincial police publisher HTML and fourteen complete normative clauses. Not a signed notice scan, not confirmation that the older three-month clause overrides later service updates or that all mixed branches apply to the pilot."}
    (ROOT / "evidence/knowledge-update/henan-norm-candidate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

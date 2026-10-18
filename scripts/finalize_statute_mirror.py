"""Archive a verified selection from the guarded official statute mirror intake."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import article_section, observe_html, parse_html, schedule, select_articles, worker_once
from intake_statute_mirror import SOURCE, URL


ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ["第二条", "第三条", "第五条", "第六条", "第七条", "第十条", "第十一条", "第十二条", "第十三条", "第十五条", "第二十一条", "第二十三条"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true", help="Use only after the updated worker is deployed")
    args = parser.parse_args()
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    intake = json.loads((ROOT / "evidence/knowledge-update/statute-mirror-intake.json").read_text(encoding="utf-8"))
    raw = (store.root / "intake-2026-10-16/statute-traffic.html").read_bytes()
    if not intake["body_acquired"] or fingerprint(raw) != intake["raw_sha256"]:
        raise ValueError("Actual guarded archive fingerprint required")
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (SOURCE,)).fetchone()[0])
    if config["seeds"] != [URL] or config["origin"] != "https://jtgl.beijing.gov.cn":
        raise ValueError("Expected already-screened official source required")
    config.update(content_selector={"id": "zoom"}, article_start_title="中华人民共和国居民身份证法", include_articles=ARTICLES,
                  article_categories={name: "eligibility" if name in ("第二条", "第五条") else "household" if name == "第十条" else
                                      "procedure" if name in ("第七条", "第十二条", "第二十一条", "第二十三条") else "identity" for name in ARTICLES},
                  metadata_selectors={"page_date": {"id": "shijian"}}, enabled=args.enable_monitoring)
    config["label"] = "居民身份证法（2011修正，附修改决定）：北京交管官方转载"
    config["metadata"].update(title="中华人民共和国居民身份证法（2011年修正，官方转载候选）", document_type="statute",
        conflict=True, initial_page_observations={"page_date": "2011-10-10", "amendment_adopted_in_body": "2011-10-29",
            "amendment_effective_in_body": "2012-01-01", "original_act_effective_article23": "2004-01-01",
            "note": "Reported page date precedes the amendment. Keep publication/reprint, act commencement and amended-version dates separate; current applicability pending."},
        corpus_identity_hint={"status": "machine_proposal_no_merge", "instrument": "中华人民共和国居民身份证法", "reported_revision": "2011修正"})
    paragraphs, _ = parse_html(raw, config["content_selector"], min_text_length=1)
    scoped, preface = article_section(paragraphs, config["article_start_title"])
    if not any("根据2011年10月29日" in item and "修正" in item for item in preface):
        raise ValueError("Expected amended-version declaration missing")
    chunks = select_articles(scoped, ARTICLES, config["article_categories"])
    observation = observe_html(raw, config["metadata_selectors"])
    observation["selected_article_preface"] = preface
    metadata = dict(config["metadata"], effective_from=None, effective_to=None, published_at=None, source_observation=observation)
    store.register(config)
    revision = store.ingest(SOURCE, URL, raw, metadata, chunks, preserve_metadata_if_unchanged=True)
    store.propose_classification(revision)
    if args.enable_monitoring:
        schedule(store)
        worker_once(store, owner="statute-mirror-intake")
    with store.db() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
        jobs = [dict(row) for row in db.execute("SELECT id,url,state,attempts,owner,error FROM jobs WHERE site_id=?", (SOURCE,))]
        site = dict(db.execute("SELECT last_success,paused_reason FROM sites WHERE id=?", (SOURCE,)).fetchone())
        if not any(job["state"] == "running" and job["owner"] == "statute-mirror-intake" for job in jobs):
            db.execute("UPDATE workers SET progress='completed' WHERE id='statute-mirror-intake'")
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE, "revision": revision,
              "original_sha256": fingerprint(raw), "url": URL, "selected_article_count": len(chunks),
              "locators": [chunk["locator"] for chunk in chunks], "selected_preface": preface,
              "automatic_monitoring_enabled": args.enable_monitoring, "jobs": jobs, "source_state": site,
              "counts": store.status()["counts"], "business_review_completed": False, "official_knowledge_published": 0,
              "scope": "Actual official HTML mirror of the already-present 2011 act, with a larger complete-clause selection. One additional archive record is not an independent new statute or a 2026 policy."}
    (ROOT / "evidence/knowledge-update/statute-mirror-candidate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

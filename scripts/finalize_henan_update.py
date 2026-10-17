"""Select only the guarded archive's province-wide renewal-window statement."""

import argparse
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import observe_html, parse_html, schedule, select_paragraph_keywords, worker_once
from nexent_api import ROOT, RUNTIME


SOURCE = "HN-UPDATE-GS-01"
URL = "https://www.gushi.gov.cn/2025/07-10/760173.html"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true", help="Requires the updated paragraph-selection worker")
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    intake = json.loads((ROOT / "evidence/knowledge-update/henan-update-intake.json").read_text(encoding="utf-8"))
    raw = (store.root / "intake-2026-10-16/henan-renewal-window.html").read_bytes()
    if not intake["body_acquired"] or fingerprint(raw) != intake["raw_sha256"] or intake["url"] != URL:
        raise ValueError("Verified guarded archive required")
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (SOURCE,)).fetchone()[0])
    if config["seeds"] != [URL] or config["origin"] != "https://www.gushi.gov.cn":
        raise ValueError("Unexpected source boundary")
    config.update(content_selector={"id": "content"}, metadata_selectors={"source_and_page_date": {"class": "sub"}},
        include_paragraph_keywords=["河南省公安厅", "身份证到期换领期限"], paragraph_expected_count=1,
        watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}], enabled=args.enable_monitoring)
    config["metadata"].update(title="事关身份证换证，办理指南请查收（河南换领期限更新线索）",
        selected_content_scope="Only the complete factual paragraph naming the provincial police renewal-window adjustment. Embedded guide image, page artwork, unrelated narration and media are not parsed or redistributed.",
        initial_page_observations={"source_named": "河南政府网", "page_date": "2025-07-10",
            "reported_change": "到期前3个月放宽到6个月", "policy_effective_date": None,
            "note": "Official government republication of service information; no original provincial adjustment instrument or expiry date established."})
    config["access_review"]["purpose"] = "bounded_daily_text_fact_monitoring" if args.enable_monitoring else "guarded_private_candidate_selection"
    paragraphs, _ = parse_html(raw, config["content_selector"])
    selected = select_paragraph_keywords(paragraphs, config["include_paragraph_keywords"], 1)
    observation = observe_html(raw, config["metadata_selectors"])
    if not any("2025-07-10" in value and "河南政府网" in value for value in observation["fields"]["block:source_and_page_date"]):
        raise ValueError("Expected visible source/date statement missing")
    metadata = dict(config["metadata"], effective_from=None, effective_to=None, published_at=None, source_observation=observation)
    store.register(config)
    revision = store.ingest(SOURCE, URL, raw, metadata, selected, preserve_metadata_if_unchanged=True)
    store.propose_classification(revision)
    if args.enable_monitoring:
        schedule(store)
        worker_once(store, owner="henan-update-intake")
    with store.db() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
        jobs = [dict(row) for row in db.execute("SELECT id,url,state,attempts,owner,error FROM jobs WHERE site_id=?", (SOURCE,))]
        state = dict(db.execute("SELECT last_success,paused_reason,failures FROM sites WHERE id=?", (SOURCE,)).fetchone())
        if not any(job["state"] == "running" and job["owner"] == "henan-update-intake" for job in jobs):
            db.execute("UPDATE workers SET progress='completed' WHERE id='henan-update-intake'")
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE, "url": URL,
        "revision": revision, "original_sha256": fingerprint(raw), "selected_paragraph_count": len(selected),
        "locators": [chunk["locator"] for chunk in selected], "unselected_paragraphs": len(paragraphs)-len(selected),
        "images_downloaded_or_parsed": 0, "page_observations": observation,
        "automatic_monitoring_enabled": args.enable_monitoring, "jobs": jobs, "source_state": state,
        "counts": store.status()["counts"], "business_review_completed": False, "official_knowledge_published": 0,
        "scope": "Actual government service-information update signal, not an original amendment instrument. One complete factual text paragraph only; no extrapolation of image/local-office details or legal commencement."}
    (ROOT / "evidence/knowledge-update/henan-update-candidate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

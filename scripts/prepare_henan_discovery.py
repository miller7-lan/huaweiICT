"""Verify the real provincial directory archive, then optionally enable bounded discovery.

Use after deploying the current worker. Directory dates and titles are signals,
not business approval. The initial archive is required only for this bootstrap.
"""

import argparse
import json
import uuid
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import create_bundle
from gov_mcp.knowledge_discovery import parse_index
from gov_mcp.knowledge_fetch import schedule, worker_once
from nexent_api import ROOT, RUNTIME


SOURCE = "HN-POLICY-DISCOVERY"
URL = "https://hnga.henan.gov.cn/zfxxgk/zc/xzgfxwj/"
OWNER_URL = "https://hnga.henan.gov.cn/2022/08-16/2561597.html"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true")
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=not args.enable_monitoring)
    intake = json.loads((ROOT / "evidence/knowledge-update/henan-policy-index-intake.json").read_text(encoding="utf-8"))
    raw = (store.root / "intake-2026-10-16/henan-policy-index.html").read_bytes()
    if not intake["body_acquired"] or intake["url"] != URL or fingerprint(raw) != intake["raw_sha256"]:
        raise ValueError("Verified guarded directory archive required")
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (SOURCE,)).fetchone()[0])
        owner = json.loads(db.execute("SELECT config FROM sites WHERE id='HN-NORM-PUBLISHER'").fetchone()[0])
        if db.execute("SELECT 1 FROM sites WHERE id IN (?,?) AND paused_reason IS NOT NULL", (SOURCE, owner["id"])).fetchone():
            raise ValueError("Paused sources require separate access review; bootstrap will not resume them")
    if config["origin"] != "https://hnga.henan.gov.cn" or config["seeds"] != [URL] or not owner["enabled"] or owner["seeds"] != [OWNER_URL]:
        raise ValueError("Expected reviewed directory and existing normative monitor required")
    config.update(enabled=args.enable_monitoring, interval_seconds=21600, allowed_paths=["/"],
        page_kind="index", discover_links=True, content_selector={"id": "content"},
        excluded_content_selectors=[{"class": "operator"}], metadata_selectors={"page_date": {"id": "pubDate"}},
        discovery_selector={"class": "zfxxgk_zdgkc"}, discovery_keywords=["居民身份证", "身份证", "修改", "废止", "清理", "便民"],
        discovery_full_title=True, discovery_upgrade_same_host_https=True,
        discovery_max_links=15, discovery_max_pages=5, discovery_max_depth=5,
        discovery_numbered_pagination={"id": "pageDec", "total_attribute": "pagecount", "size_attribute": "pagesize", "url_pattern": "index_{page}.html"},
        discovery_document_owners={OWNER_URL: owner["id"]},
        watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}])
    config["metadata"].update(region="unknown", matter="unclassified", document_type="official_policy_candidate", scope_confirmed=False,
        title="省厅目录发现的待核定文件", collection_role="discovery_candidate",
        selected_content_scope="Only matched normative-file directory entries and their selected HTML body; amendments/repeal lists and convenience measures included. Linked attachments are not fetched automatically.")
    config["access_review"]["purpose"] = "bounded_official_normative_directory_discovery_pending_scope_review"
    entries, truncated = parse_index(raw, "index", URL, config)
    if truncated or not any(entry["url"] == OWNER_URL for entry in entries) or not any(entry["url"].endswith("/index_1.html") for entry in entries):
        raise ValueError("Observed directory/parser contract changed; review it before enabling")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE,
        "initial_index_sha256": fingerprint(raw), "initial_selected_entries": entries,
        "pagination_basis": "Actual rendered next-page click on 2026-10-16 reached index_1.html and showed 2/2; raw page declares 41 records at 24/page. Reviewed pattern regenerates bounded pages from each returned total.",
        "same_host_http_links_upgraded_to_https": True, "business_review_completed": False,
        "automatic_monitoring_enabled": args.enable_monitoring,
        "scope": "Bounded declared provincial normative-file directory, not a claim of all provincial policies or attachment acquisition. Unknown applicability stays pending; the existing 14-clause monitor owns its exact URL."}
    if args.enable_monitoring:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
        create_bundle(store, store.root / "backups" / (stamp+"-before-henan-discovery"))
        store.register(config)
        schedule(store)
        for _ in range(35):
            if not worker_once(store, owner="henan-discovery-bootstrap"):
                break
        with store.db() as db:
            db.execute("UPDATE workers SET progress='completed' WHERE id='henan-discovery-bootstrap'")
            configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
            report["jobs"] = [dict(row) for row in db.execute("SELECT url,state,attempts,error FROM jobs WHERE site_id=?", (SOURCE,))]
            report["index_checks"] = [json.loads(row[0]) for row in db.execute("SELECT data FROM audit WHERE action='discovery_index_checked' AND json_extract(data,'$.site')=? ORDER BY id DESC LIMIT 5", (SOURCE,))]
            report["owner_document_count"] = db.execute("SELECT COUNT(*) FROM documents WHERE url=?", (OWNER_URL,)).fetchone()[0]
        (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
        status = store.status()
        report.update(counts=status["counts"], discovery=status["discovery"], official_knowledge_published=status["counts"]["heads"])
        report["source_check_passed"] = bool(report["jobs"]) and all(job["state"] == "done" for job in report["jobs"]) and report["owner_document_count"] == 1
        (ROOT / "evidence/knowledge-update/henan-policy-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.enable_monitoring and not report["source_check_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

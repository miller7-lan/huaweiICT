"""Add the publisher's missing policy-explanation directory to normal discovery."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_discovery import parse_index
from gov_mcp.knowledge_fetch import schedule, worker_once
from nexent_api import ROOT, RUNTIME


SOURCE = "HN-INTERPRETATION-DISCOVERY"
URL = "https://hnga.henan.gov.cn/zfxxgk/zfxxgkml/zcjd/"
OWNER_URL = "https://hnga.henan.gov.cn/2026/05-11/3349863.html"


def main():
    parser = argparse.ArgumentParser(description="把省厅政策解读目录接入既有发现链；不发布知识")
    parser.add_argument("--research-directory", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    research = args.research_directory.resolve()
    raw = (research / "henan-interpretation-index.html").read_bytes()
    receipt = json.loads((research / "henan-interpretation-index-result.json").read_text(encoding="utf-8"))
    if receipt.get("status") != 200 or receipt["url"] != URL or fingerprint(raw) != receipt["sha256"]:
        raise ValueError("Earlier normal Fetcher directory receipt required")
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=args.verify_only)
    stamp = datetime.now(timezone.utc).isoformat()
    basis = "Official police policy-explanation directory. Limited private archive and attributed policy-fact analysis; no media/design redistribution. Matched convenience measures may contain multiple matters and stay unclassified pending scope review. Exact known 2026 resident-ID measure belongs to its existing bounded monitor."
    config = {"id": SOURCE, "label": "河南省公安厅政策解读目录：身份证及便民利民更新发现", "origin": "https://hnga.henan.gov.cn",
        "allowed_paths": ["/"], "seeds": [URL], "enabled": True, "interval_seconds": 21600, "delay_seconds": 5,
        "page_kind": "index", "discover_links": True, "content_selector": {"id": "content"},
        "metadata_selectors": {"page_date": {"id": "pubDate"}, "named_source": {"id": "source"}},
        "discovery_selector": {"class": "listbox"}, "discovery_keywords": ["居民身份证", "便民利民"],
        "discovery_full_title": True, "discovery_upgrade_same_host_https": True,
        "discovery_max_links": 10, "discovery_max_pages": 3, "discovery_max_depth": 3,
        "discovery_numbered_pagination": {"id": "pageDec", "total_attribute": "pagecount", "size_attribute": "pagesize", "url_pattern": "index_{page}.html"},
        "discovery_document_owners": {OWNER_URL: "HN-MEASURES-2026"},
        "watch_scopes": [{"region": "郑州市二七区", "matter": "demo-id-renewal"}],
        "metadata": {"title": "政策解读目录发现的待分类文件", "region": "unknown", "matter": "unclassified",
            "document_type": "official_policy_explanation", "collection_role": "discovery_candidate", "scope_confirmed": False,
            "effective_from": None, "effective_to": None, "published_at": None, "usage_basis": basis,
            "business_review": "pending", "selected_content_scope": "Complete selected HTML paragraphs of matched explanation pages. Other matters are not approved for resident-ID answers; future image-only or unsupported bodies quarantine normally. No automatic media download."},
        "access_review": {"approved": True, "reviewer": "Codex AI technical access/use screen", "actor_kind": "machine",
            "checked_at": stamp, "usage_basis": basis, "business_review_completed": False,
            "purpose": "bounded_official_policy_explanation_discovery_pending_scope_review"}}
    entries, truncated = parse_index(raw, "index", URL, config)
    if truncated or not any(e["url"] == OWNER_URL for e in entries) or not any(e["kind"] == "index" for e in entries):
        raise ValueError("Observed directory and pagination contract changed")
    if not args.verify_only:
        with store.db() as db:
            if db.execute("SELECT 1 FROM sites WHERE id=?", (SOURCE,)).fetchone():
                raise ValueError("Already registered; use normal monitoring or --verify-only")
        store.register(config)
        schedule(store)
        # A finite bootstrap; durable background work owns anything not done here.
        for _ in range(12):
            with store.db() as db:
                active = db.execute("SELECT 1 FROM jobs WHERE site_id IN (?,?) AND state='queued'", (SOURCE, "HN-MEASURES-2026")).fetchone()
            if not active or not worker_once(store, owner="henan-interpretation-intake"):
                break
    with store.db() as db:
        current = db.execute("SELECT * FROM sites WHERE id=?", (SOURCE,)).fetchone()
        observed = json.loads(current["config"])
        if observed["seeds"] != [URL] or observed["discovery_document_owners"] != {OWNER_URL: "HN-MEASURES-2026"}:
            raise ValueError("Expected directory and owner boundaries differ")
        jobs = [dict(r) for r in db.execute("SELECT id,url,state,attempts,error FROM jobs WHERE site_id=? ORDER BY id", (SOURCE,))]
        source_documents = [dict(r) for r in db.execute("SELECT d.url,r.id AS revision,r.sha256,r.state FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)", (SOURCE,))]
        owner_count = db.execute("SELECT COUNT(*) FROM documents WHERE url=?", (OWNER_URL,)).fetchone()[0]
        audit = [json.loads(r[0]) for r in db.execute("SELECT data FROM audit WHERE action='discovery_index_checked' AND json_extract(data,'$.site')=? ORDER BY id DESC LIMIT 3", (SOURCE,))]
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
        if not args.verify_only and not db.execute("SELECT 1 FROM jobs WHERE owner='henan-interpretation-intake' AND state='running'").fetchone():
            db.execute("UPDATE workers SET progress='completed' WHERE id='henan-interpretation-intake'")
    if not args.verify_only:
        (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": stamp, "source_id": SOURCE, "url": URL, "automatic_monitoring_enabled": observed["enabled"],
        "initial_index_sha256": receipt["sha256"], "initial_entries": entries, "jobs": jobs, "discovered_documents": source_documents,
        "index_observations": audit, "existing_2026_document_count": owner_count, "paused_reason": current["paused_reason"],
        "read_only_verification": args.verify_only, "counts": store.status()["counts"],
        "passed": bool(jobs) and all(j["state"] == "done" and not j["error"] for j in jobs) and owner_count == 1 and current["paused_reason"] is None,
        "business_review_completed": False, "scope": "Declared policy-explanation directory only, with bounded numbered pagination and explicit delegation. Not all official policy coverage or approval of other matters."}
    (ROOT / "evidence/knowledge-update/henan-interpretation-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "jobs": len(jobs), "new_documents": len(source_documents), "owner_count": owner_count, "counts": report["counts"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

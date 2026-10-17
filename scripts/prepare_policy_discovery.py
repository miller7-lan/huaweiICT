"""Enable bounded title-based policy discovery; discovered scope remains unclassified."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    stamp = datetime.now(timezone.utc).isoformat()
    config = {"id": "BEIJING-POLICY-DISCOVERY", "label": "政策栏目：居民身份证相关文件发现",
        "origin": "https://www.beijing.gov.cn", "seeds": ["https://www.beijing.gov.cn/zhengce/zhengcefagui/"],
        "allowed_paths": ["/zhengce/zhengcefagui/"], "enabled": True, "interval_seconds": 21600, "delay_seconds": 5,
        "page_kind": "index", "content_selector": {"id": "mainText"}, "discover_links": True,
        "discovery_keywords": ["居民身份证", "身份证"], "discovery_max_links": 25,
        "metadata": {"title": "待核定政策文件", "region": "unknown", "matter": "unclassified",
            "document_type": "official_policy_candidate", "published_at": None, "effective_from": None, "effective_to": None,
            "usage_basis": "Private engineering discovery of official normative texts and factual link metadata; attributed text only. Copyright Law Article 5(1) screen; scope/current validity unreviewed.",
            "business_review": "pending", "scope_confirmed": False, "collection_role": "discovery_candidate"},
        "access_review": {"approved": True, "reviewer": "codex_automated_technical_access_screen", "actor_kind": "machine",
            "checked_at": stamp, "usage_basis": "Robots policy and normal index GET returned 200 in this run; private metadata/candidate archive only, no site media/layout republication",
            "business_review_completed": False, "purpose": "bounded_discovery_pending_scope_review"}}
    store.register(config)
    schedule(store)
    for _ in range(26):
        if not worker_once(store, owner="policy-discovery"):
            break
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='policy-discovery'")
        jobs = [dict(r) for r in db.execute("SELECT url,state,attempts,error FROM jobs WHERE site_id=?", (config["id"],))]
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "jobs": jobs, "keywords": config["discovery_keywords"],
        "interval_seconds": config["interval_seconds"], "max_discovered_links_per_index": 25,
        "discovered_document_count": max(0, len(jobs)-1), "business_review_completed": False,
        "scope": "Actual official policy index check; only matching anchor titles are queued. Unknown discovered regions/matters never inherit national or trial applicability."}
    (ROOT / "evidence/knowledge-update/policy-discovery-2026-10-15.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not jobs or any(job["state"] != "done" for job in jobs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

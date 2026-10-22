"""Upgrade the existing approved discovery entry, preserving access reviews and pauses.

Stop this runtime's services before running; restart them afterwards. No new source
is enabled and no new access authorization is inferred by this migration.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import create_bundle
from gov_mcp.knowledge_discovery import parse_index
from gov_mcp.knowledge_fetch import schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "BEIJING-POLICY-DISCOVERY"


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    with store.db() as db:
        configs = {row["id"]: json.loads(row["config"]) for row in db.execute("SELECT id,config FROM sites")}
        pauses = {row["id"]: row["paused_reason"] for row in db.execute("SELECT id,paused_reason FROM sites")}
        if db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone():
            raise RuntimeError("Wait for the owned worker to finish before stopping and migrating")
    config = dict(configs[SOURCE])
    expected = "https://www.beijing.gov.cn/zhengce/zhengcefagui/"
    if config["origin"] != "https://www.beijing.gov.cn" or config["seeds"] != [expected] or pauses[SOURCE]:
        raise RuntimeError("Existing approved and unpaused discovery source required")
    config.update(discovery_selector={"class": "default_news"}, discovery_max_pages=10,
                  discovery_max_depth=10, watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}])
    archived_path = store.root / "intake-2026-10-15/policy-index.html"
    archived = archived_path.read_bytes()
    all_config = dict(config, discovery_keywords=[])
    archived_all, all_truncated = parse_index(archived, "index", expected, all_config)
    archived_matches, matching_truncated = parse_index(archived, "index", expected, config)
    if not archived_all:
        raise RuntimeError("Configured list selector did not find archived policy anchors")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    create_bundle(store, store.root / "backups" / (stamp+"-before-discovery-upgrade"))
    store.register(config)
    with store.db() as db:
        after = {row["id"]: json.loads(row["config"]) for row in db.execute("SELECT id,config FROM sites")}
        assert after[SOURCE]["access_review"] == configs[SOURCE]["access_review"]
        assert all(value == after[source_id] for source_id, value in configs.items() if source_id != SOURCE)
        assert pauses == {row["id"]: row["paused_reason"] for row in db.execute("SELECT id,paused_reason FROM sites")}
    (store.root / "sources.json").write_text(json.dumps({"sources": list(after.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
    schedule(store)
    # The single existing HTML entry is checked through the normal robots/rate/IP
    # guarded worker. No invented pagination URLs or alternate bot identities.
    for _ in range(26):
        if not worker_once(store, owner="discovery-upgrade"):
            break
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='discovery-upgrade'")
        jobs = [dict(row) for row in db.execute("SELECT url,state,attempts,error FROM jobs WHERE site_id=?", (SOURCE,))]
        audits = [json.loads(row[0]) for row in db.execute("SELECT data FROM audit WHERE action='discovery_index_checked' AND json_extract(data,'$.site')=? ORDER BY id DESC LIMIT 5", (SOURCE,))]
    status = store.status()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE,
              "archived_selector_check": {"raw_sha256": fingerprint(archived), "all_policy_links": len(archived_all),
                  "matching_links": len(archived_matches), "truncated": all_truncated or matching_truncated,
                  "current_network_evidence": False,
                  "pagination": "Archived site JS slices the returned list client-side; parser scans the returned list, without invented page URLs"},
              "access_review_preserved": True, "other_source_configs_preserved": True,
              "preexisting_pause_states_preserved_during_migration": True,
              "jobs": jobs, "discovery": status["discovery"], "index_checks": audits,
              "counts": status["counts"], "business_review_completed": False,
              "rss_atom_sitemap_real_sources_enabled": False,
              "scope": "Actual existing HTML source migration/check; RSS/Atom/sitemap behavior is covered separately by controlled fixtures"}
    report["source_check_passed"] = bool(jobs) and all(job["state"] == "done" for job in jobs)
    (ROOT / "evidence/knowledge-update/discovery-upgrade.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["source_check_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Enable publisher-bound attachment discovery without broadening existing source grants."""

import argparse
import copy
import json
import uuid
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_backup import create_bundle
from gov_mcp.knowledge_fetch import schedule, worker_once
from nexent_api import ROOT, RUNTIME


PARENT = "HN-POLICY-DISCOVERY"
COLLECTORS = {"HN-OSS-PDF-DISCOVERY": "https://oss.henan.gov.cn", "HN-XCOSS-PDF-DISCOVERY": "https://xcoss.henan.gov.cn"}
EXISTING = {"HN-MEASURES-2021-PDF": "monitor", "HN-REPEAL-2020-PDF": "monitor", "HN-REPEAL-2021-PDF": "monitor",
            "HN-VALIDITY-2025-PDF": "excluded_history", "HN-REPEAL-2025B-PDF": "excluded_history"}


def collector_configs(parent):
    basis = ("Machine technical access/use screen limited to PDF normative instruments directly linked in the selected body of official Henan provincial police normative-file publications from HN-POLICY-DISCOVERY. "
        "Only the declared HTTPS origin and /typtfile/YYYYMMDD/32-hex.pdf layout; publisher identity, visible parent link and separate normal robots/IP/TLS/rate checks required. "
        "Private integrity archive and attributed normative-text analysis under Copyright Law Article 5(1); not a blanket license for object-storage contents, news, media or website design. "
        "No business applicability or effectivity approval; unknown origins, parameterized links and unsupported formats require separate review.")
    return [{"id": source_id, "label": "省厅规范PDF附件发现："+origin.split("//")[1], "origin": origin, "allowed_paths": ["/typtfile/"],
        "seeds": [], "enabled": True, "interval_seconds": 86400, "delay_seconds": 5, "page_kind": "document", "discover_links": False,
        "attachment_only": True, "attachment_parent_sources": [PARENT], "attachment_url_layout": "henan_typtfile_pdf", "attachment_max_pending": 20,
        "pdf_ocr": {"engine": "windows.media.ocr", "language": "zh-Hans-CN"}, "watch_scopes": copy.deepcopy(parent["watch_scopes"]),
        "metadata": {"title": "待核查省厅规范PDF附件", "region": "unknown", "matter": "unclassified", "scope_confirmed": False,
            "document_type": "official_normative_attachment_candidate", "effective_from": None, "effective_to": None, "published_at": None,
            "usage_basis": basis, "collection_role": "attachment_candidate", "business_review": "pending"},
        "access_review": {"approved": True, "reviewer": "Codex AI bounded official normative attachment technical screen", "actor_kind": "machine",
            "checked_at": datetime.now(timezone.utc).isoformat(), "usage_basis": basis, "business_review_completed": False,
            "purpose": "publisher_bound_normative_pdf_discovery_not_general_bucket_crawling"}} for source_id, origin in COLLECTORS.items()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true", required=True)
    parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    with store.db() as db:
        configs = {r["id"]: json.loads(r["config"]) for r in db.execute("SELECT id,config FROM sites")}
        if any(source_id in configs for source_id in COLLECTORS) or configs[PARENT].get("attachment_discovery"):
            raise ValueError("Attachment migration already configured; use normal monitored jobs")
        if db.execute("SELECT 1 FROM sites WHERE id=? AND paused_reason IS NOT NULL", (PARENT,)).fetchone():
            raise ValueError("Will not resume a paused publisher")
        exclusions = [dict(r) for r in db.execute("SELECT d.id AS document_id,r.id,r.sha256,r.body_sha256,e.reviewer,e.reason FROM revisions r JOIN documents d ON d.id=r.document_id JOIN exclusions e ON e.revision_id=r.id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)")]
        owners = {}
        for source_id, policy in EXISTING.items():
            config = configs[source_id]
            if len(config["seeds"]) != 1:
                raise ValueError("Exact existing attachment seed required")
            url = config["seeds"][0]
            row = db.execute("SELECT r.id,r.sha256,r.state FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (source_id, url)).fetchone()
            if not row:
                raise ValueError("Existing attachment archive required")
            store._verify(db, row["id"], require_review=False)
            owners[url] = {"source_id": source_id, "policy": policy}
            if policy == "excluded_history":
                if config["enabled"] or row["state"] != "excluded" or config["metadata"].get("collection_role") != "governance_reference":
                    raise ValueError("Historical attachment exclusion binding differs")
                owners[url]["sha256"] = row["sha256"]
    parent = copy.deepcopy(configs[PARENT])
    parent["attachment_discovery"] = {"sources": list(COLLECTORS), "owners": owners, "max_links": 5}
    parent["metadata"]["selected_content_scope"] = "Matched official normative-file directory HTML bodies; PDF bodies use separate publisher-bound reviewed attachment sources. Existing monitors are reused; explicitly excluded historical scans are not re-fetched."
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    create_bundle(store, store.root / "backups" / (stamp+"-before-attachment-discovery"))
    for config in collector_configs(parent):
        store.register(config)
    store.register(parent)
    schedule(store)
    for _ in range(40):
        if not worker_once(store, owner="attachment-discovery-bootstrap"):
            break
    reapplied, changed = [], []
    for previous in exclusions:
        with store.db() as db:
            latest = db.execute("SELECT * FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (previous["document_id"],)).fetchone()
            store._verify(db, latest["id"], require_review=False)
        if latest["state"] == "excluded":
            continue
        if (latest["state"] == "pending_review" and latest["sha256"] == previous["sha256"] and latest["body_sha256"] == previous["body_sha256"]
                and previous["reviewer"].startswith("Codex AI")):
            revision = store.exclude(latest["id"], "Codex AI attachment migration source-scope check",
                "新增附件来源/定位元数据；已校验原件字节和选入正文均与前次AI范围核查一致，继续保留原排除理由："+previous["reason"], True)
            reapplied.append({"previous": previous["id"], "current": revision, "original_and_body_unchanged": True})
        else:
            changed.append(latest["id"])
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='attachment-discovery-bootstrap'")
        all_configs = {r["id"]: json.loads(r["config"]) for r in db.execute("SELECT id,config FROM sites")}
        proof_ids = sorted({PARENT, *COLLECTORS, *(key for key, policy in EXISTING.items() if policy == "monitor"), *parent.get("discovery_document_owners", {}).values()})
        jobs = [dict(r) for r in db.execute("SELECT site_id,url,state,attempts,error FROM jobs WHERE site_id IN ("+",".join("?" for _ in proof_ids)+")", proof_ids)]
        ownership_counts = {url: db.execute("SELECT COUNT(*) FROM documents WHERE url=?", (url,)).fetchone()[0] for url in owners}
    if any(all_configs[key] != value for key, value in configs.items() if key != PARENT):
        raise ValueError("Unrelated source configuration changed")
    (store.root / "sources.json").write_text(json.dumps({"sources": list(all_configs.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
    status = store.status()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(j["state"] == "done" for j in jobs) and all(n == 1 for n in ownership_counts.values()),
        "parent_source": PARENT, "collector_sources": list(COLLECTORS), "jobs": jobs, "attachments": status["attachments"],
        "counts": status["counts"], "waiting_collectors": [s["id"] for s in status["sites"] if s["waiting_for_attachment"]],
        "known_attachment_document_counts": ownership_counts,
        "scope_exclusions_reapplied_after_exact_original_and_body_check": reapplied, "new_changes_left_pending": changed,
        "unrelated_source_configs_preserved": True, "parent_access_review_preserved": configs[PARENT]["access_review"] == all_configs[PARENT]["access_review"],
        "business_review_completed": False, "human_attestations_created": 0,
        "scope": "Actual bounded publisher migration and rechecks. Known files retain their source ownership; historical exemptions are explicit and do not claim a fresh PDF check. No claim that a new official file was published during this run."}
    (ROOT / "evidence/knowledge-update/attachment-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "counts": report["counts"], "attachment_parents": len(report["attachments"]), "waiting_collectors": report["waiting_collectors"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

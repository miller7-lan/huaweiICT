"""First guarded intake of the two reviewed Henan entry points on a fresh runtime."""

import argparse
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import Fetcher
from gov_mcp.knowledge_lock import process_lock
from nexent_api import ROOT, RUNTIME


TARGETS = {
    "service-update": {"id": "HN-UPDATE-GS-01", "origin": "https://www.gushi.gov.cn", "path": "/2025/07-10/760173.html",
        "label": "河南换领期限调整：固始县政府转载线索", "type": "official_service_information", "role": "update_signal",
        "archive": "henan-renewal-window.html", "report": "henan-update-intake.json",
        "basis": "Official government service-information republication. Technical use screen limited to the necessary factual statement on the reported province-wide renewal window, private integrity archive and attributed factual summary. No illustration/media download or redistribution of the full news page; no inference that every site item is licensed. Current policy applicability is not approved by this screen."},
    "norm-publisher": {"id": "HN-NORM-PUBLISHER", "origin": "https://hnga.henan.gov.cn", "path": "/2022/08-16/2561597.html",
        "label": "河南省规范：省公安厅原始发布页面候选", "type": "provincial_normative_text_candidate", "role": "core_candidate",
        "archive": "henan-norm-publisher.html", "report": "henan-norm-publisher-intake.json",
        "basis": "Official provincial police normative/administrative document technical use screen under Copyright Law Article 5(1); private original and attributed rule excerpts, no site illustrations, media or design redistribution. Source identity is not current applicability or business approval."},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=TARGETS)
    parser.add_argument("--validate-only", action="store_true", help="Check an existing guarded archive; no network or source mutation")
    args = parser.parse_args()
    target = TARGETS[args.source]
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=args.validate_only)
    archive = store.root / "intake-2026-10-16" / target["archive"]
    report_path = ROOT / "evidence/knowledge-update" / target["report"]
    url = target["origin"]+target["path"]
    if args.validate_only:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not report["body_acquired"] or report["url"] != url or report["raw_sha256"] != fingerprint(archive.read_bytes()):
            raise ValueError("Verified original archive missing or changed")
        print(json.dumps({"source": target["id"], "archive_verified": True, "network_requests": 0}, ensure_ascii=False))
        return
    with process_lock(store.root / "source-intake.lock") as acquired:
        if not acquired:
            raise RuntimeError("Another source intake is active")
        with store.db() as db:
            if db.execute("SELECT 1 FROM sites WHERE id=?", (target["id"],)).fetchone():
                raise ValueError("Source already exists; use validate-only or its normal monitored worker, not another first intake")
        stamp = datetime.now(timezone.utc).isoformat()
        config = {"id": target["id"], "label": target["label"], "origin": target["origin"],
            "allowed_paths": [target["path"]], "seeds": [url], "enabled": False, "interval_seconds": 86400, "delay_seconds": 5,
            "page_kind": "document", "discover_links": False,
            "metadata": {"title": target["label"], "region": "河南省", "matter": "demo-id-renewal", "document_type": target["type"],
                "scope_confirmed": False, "collection_role": target["role"], "effective_from": None, "effective_to": None,
                "published_at": None, "usage_basis": target["basis"], "business_review": "pending"},
            "access_review": {"approved": True, "actor_kind": "machine", "reviewer": "Codex AI technical access/use screen",
                "checked_at": stamp, "usage_basis": target["basis"], "business_review_completed": False,
                "purpose": "one_time_guarded_private_intake_before_parser_enablement"}}
        store.register(config)
        report = {"source_id": target["id"], "url": url, "captured_at": stamp, "body_acquired": False,
            "body_ingested": False, "automatic_monitoring_enabled": False, "business_review_completed": False}
        try:
            status, headers, raw, final = Fetcher(store).fetch(dict(config, enabled=True), url)
            report.update(status=status, content_type=headers.get("content-type"), final_url=final)
            if status != 200 or headers.get("content-type", "").split(";")[0] not in ("text/html", "application/xhtml+xml"):
                raise ValueError("Expected normal official HTML page not returned")
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(raw)
            report.update(body_acquired=True, bytes=len(raw), raw_sha256=fingerprint(raw))
        except (OSError, ValueError) as error:
            report.update(error_type=type(error).__name__, error=str(error)[:250])
            with store.db() as db:
                db.execute("UPDATE sites SET paused_reason=? WHERE id=?", (str(error)[:250], target["id"]))
                store.log(db, "manual_source_intake_stopped", {"source": target["id"], "reason": str(error)[:250]})
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["body_acquired"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

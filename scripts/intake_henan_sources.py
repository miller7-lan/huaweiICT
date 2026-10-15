"""First guarded intake of reviewed Henan entry points on a fresh runtime."""

import argparse
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import Fetcher
from gov_mcp.knowledge_lock import process_lock
from nexent_api import ROOT, RUNTIME


TARGETS = {
    "validity-pdf": {"id": "HN-VALIDITY-2025-PDF", "origin": "https://xcoss.henan.gov.cn", "path": "/typtfile/20251021/417a0dee18aa447c9caf64d5f36473eb.pdf",
        "label": "2025栏目载入的省厅2019效力清理通知：扫描附件", "type": "official_normative_validity_list", "role": "validity_candidate",
        "archive": "henan-current-list-2025.pdf", "report": "henan-validity-pdf-intake.json", "media_type": "application/pdf",
        "basis": "Exact PDF attachment directly linked by the provincial police normative-file publication https://hnga.henan.gov.cn/2025/10-21/3237047.html. Private integrity archive and attributed legal/administrative normative-text analysis under Copyright Law Article 5(1). This screen covers this attachment only, not other object-storage files; present applicability is pending source review."},
    "repeal-pdf": {"id": "HN-REPEAL-2025B-PDF", "origin": "https://xcoss.henan.gov.cn", "path": "/typtfile/20251021/ca68ab971dee440089901776f99dbdcf.pdf",
        "label": "2025栏目载入的省厅2022废止通知：扫描附件", "type": "official_normative_repeal_list", "role": "validity_candidate",
        "archive": "henan-repeal-2025.pdf", "report": "henan-repeal-pdf-intake.json", "media_type": "application/pdf",
        "basis": "Exact PDF attachment directly linked by the provincial police normative-file publication https://hnga.henan.gov.cn/2025/10-21/3237046.html. Private integrity archive and attributed legal/administrative normative-text analysis under Copyright Law Article 5(1). This screen covers this attachment only, not other object-storage files; present applicability is pending source review."},
    "policy-index": {"id": "HN-POLICY-DISCOVERY", "origin": "https://hnga.henan.gov.cn", "path": "/zfxxgk/zc/xzgfxwj/",
        "label": "河南省公安厅行政规范性文件栏目", "type": "official_policy_index", "role": "discovery_candidate",
        "archive": "henan-policy-index.html", "report": "henan-policy-index-intake.json",
        "basis": "Official provincial police administrative normative-file directory. Technical use screen limited to bounded link metadata discovery and private integrity archive of the directory, then separately guarded retrieval of official normative texts. No media/design redistribution or general permission for other website material; discovered policy scope and current applicability remain pending."},
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
            expected = ("application/pdf",) if target.get("media_type") == "application/pdf" else ("text/html", "application/xhtml+xml")
            if status != 200 or headers.get("content-type", "").split(";")[0] not in expected:
                raise ValueError("Expected normal official document format not returned")
            if target.get("media_type") == "application/pdf" and not raw.startswith(b"%PDF-"):
                raise ValueError("Expected PDF header missing")
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

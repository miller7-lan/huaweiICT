"""One guarded official legal-text retrieval; automatic discovery stays disabled.

This archive step does not ingest or publish an unchecked selection. Inspect the
actual body/selector before creating a candidate or enabling recurring checks.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import Fetcher


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "ID-AMENDMENT-JT-LEAD"
URL = "https://jtgl.beijing.gov.cn/jgj/jgxx/flfg/fl/203777/index.html"


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    stamp = datetime.now(timezone.utc).isoformat()
    basis = "Official legislative text, Copyright Law Article 5(1) technical screen; private candidate archive and attributed excerpts only, no website media/design redistribution. https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html"
    config = {"id": SOURCE, "label": "居民身份证法修改决定及文本：北京交管官方转载候选",
        "origin": "https://jtgl.beijing.gov.cn", "allowed_paths": ["/jgj/jgxx/flfg/fl/203777/index.html"], "seeds": [URL],
        "enabled": False, "interval_seconds": 86400, "delay_seconds": 5, "page_kind": "document", "discover_links": False,
        "metadata": {"title": "居民身份证法修改决定及文本（选入范围待核对）", "region": "national", "matter": "demo-id-renewal",
            "document_type": "legislative_text_candidate", "effective_from": None, "effective_to": None, "published_at": None,
            "scope_confirmed": True, "usage_basis": basis, "business_review": "pending", "collection_role": "core_candidate"},
        "access_review": {"approved": True, "actor_kind": "machine", "reviewer": "Codex AI technical access screen",
            "checked_at": stamp, "usage_basis": basis, "business_review_completed": False,
            "purpose": "one_time_guarded_private_intake_before_parser_enablement",
            "screening_note": "Prior normal robots probe returned 404; the Fetcher rechecks robots/TLS/paths/rate for this one-time request. This is not a statement of site-wide permission."}}
    with store.db() as db:
        if db.execute("SELECT 1 FROM sites WHERE id=?", (SOURCE,)).fetchone():
            raise RuntimeError("Existing intake state must be inspected before another attempt")
    store.register(config)
    report = {"started_at": stamp, "source_id": SOURCE, "url": URL, "automatic_collection_enabled": False,
              "body_acquired": False, "body_ingested": False, "business_review_completed": False, "official_publication": False}
    try:
        # The registered row persists the normal per-origin rate reservation;
        # this local call is explicitly enabled for the single reviewed request.
        manual = dict(config, enabled=True)
        status, headers, raw, final = Fetcher(store).fetch(manual, URL)
        report.update(status=status, content_type=headers.get("content-type"), final_url=final)
        if status != 200 or headers.get("content-type", "").split(";")[0] not in ("text/html", "application/xhtml+xml"):
            raise ValueError("Expected normal official HTML body was not returned")
        directory = store.root / "intake-2026-10-16"
        directory.mkdir(exist_ok=True)
        path = directory / "statute-traffic.html"
        path.write_bytes(raw)
        report.update(body_acquired=True, bytes=len(raw), raw_sha256=fingerprint(raw))
    except (OSError, ValueError) as error:
        report.update(error_type=type(error).__name__, error=str(error)[:300])
        with store.db() as db:
            db.execute("UPDATE sites SET paused_reason=? WHERE id=?", (str(error)[:300], SOURCE))
            store.log(db, "manual_source_intake_stopped", {"source": SOURCE, "reason": str(error)[:300]})
    with store.db() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report["counts"] = store.status()["counts"]
    (ROOT / "evidence/knowledge-update/statute-mirror-intake.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

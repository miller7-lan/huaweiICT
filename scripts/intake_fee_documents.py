"""Fetch two official normative PDFs as unapproved candidates; keep effectivity unresolved."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ("NDRC-FEE-2003", "国家发展改革委 财政部关于居民身份证收费标准及有关问题的通知", "2022101017594886.pdf", "发改价格〔2003〕2322号", "2003-12-30"),
    ("NDRC-FEE-2005", "国家发展改革委 财政部 公安部关于规范居民身份证工本费标准等有关问题的通知", "2022101016343695.pdf", "发改价格〔2005〕436号", "2005-03-18"),
]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    stamp = datetime.now(timezone.utc).isoformat()
    basis = ("Official administrative normative text; limited private archive and attributed excerpts under Copyright Law Article 5(1). "
             "No website media or design redistribution, no assertion of present effectivity. "
             "https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html")
    for source_id, title, filename, number, signed in SPECS:
        path = "/upload/images/202210/"+filename
        config = {"id": source_id, "label": title, "origin": "https://zfxxgk.ndrc.gov.cn", "enabled": True,
            "allowed_paths": [path], "seeds": ["https://zfxxgk.ndrc.gov.cn"+path], "interval_seconds": 86400,
            "delay_seconds": 5, "discover_links": False, "page_kind": "document",
            "metadata": {"title": title, "region": "national", "matter": "demo-id-renewal", "document_type": "administrative_norm",
                "issuer": "国家发展改革委、财政部" + ("、公安部" if source_id.endswith("2005") else ""),
                "document_number": number, "published_at": None, "effective_from": None, "effective_to": None,
                "usage_basis": basis, "business_review": "pending", "collection_role": "fee_candidate",
                "initial_page_observations": {"signed_at_pdf": signed, "storage_directory_date_is_not_effective_date": True},
                "conflict": source_id.endswith("2003"),
                "unresolved_notes": ["Verify present validity, subsequent amendments/exemptions and branch-specific applicability before publication"] +
                    (["A 2017 proposed-repeal list also mentions this document. A proposal is not proof of repeal; resolve against subsequent official decisions and current normative catalog."] if source_id.endswith("2003") else [])},
            "access_review": {"approved": True, "reviewer": "codex_automated_technical_access_screen", "actor_kind": "machine",
                "checked_at": stamp, "usage_basis": basis, "business_review_completed": False,
                "purpose": "private_engineering_candidates_only", "robots_url": "https://zfxxgk.ndrc.gov.cn/robots.txt",
                "screening_note": "Every actual request remains conditional on the Fetcher robots, rate, public-IP and redirect checks"}}
        store.register(config)
    lead = {"id": "HN-NORM-XY-LEAD", "label": "河南省公安机关户口居民身份证管理工作规范（试行）官方转载线索",
        "origin": "https://gaj.xinyang.gov.cn", "enabled": False, "allowed_paths": ["/2025/04-25/600324.html"],
        "seeds": ["https://gaj.xinyang.gov.cn/2025/04-25/600324.html"], "interval_seconds": 86400, "delay_seconds": 5,
        "metadata": {"title": "河南省公安机关户口居民身份证管理工作规范（试行）", "region": "河南省", "matter": "demo-id-renewal",
            "document_type": "administrative_norm", "effective_from": None, "effective_to": None, "usage_basis": None,
            "collection_role": "core_candidate", "business_review": "pending"},
        "access_review": {"approved": False, "reason": "Read-only web tool returned page HTTP 403; no automated body acquisition or bypass attempted"}}
    store.register(lead)
    with store.db() as db:
        db.execute("UPDATE sites SET paused_reason=? WHERE id=?", ("Official page returned HTTP 403 during read-only discovery; disabled", lead["id"]))
    schedule(store)
    for _ in SPECS:
        worker_once(store, owner="fee-intake")
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='fee-intake'")
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
        rows = [dict(r) for r in db.execute("SELECT r.id,r.sha256,r.state,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id IN ('NDRC-FEE-2003','NDRC-FEE-2005')")]
        jobs = [dict(r) for r in db.execute("SELECT site_id,state,attempts,error FROM jobs WHERE site_id IN ('NDRC-FEE-2003','NDRC-FEE-2005')")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": stamp, "revisions": rows, "jobs": jobs, "counts": store.status()["counts"],
        "provincial_lead": {"id": lead["id"], "status": "disabled_after_page_403", "new_body_acquired": False},
        "validity_issue_source": "https://www.ndrc.gov.cn/yjzxDownload/fgsfzwjt20170801t02.pdf",
        "business_review_completed": False, "official_knowledge_published": 0,
        "note": "Historic fee-related administrative documents, not new 2026 rules; current validity/fee conclusions unresolved"}
    (ROOT / "evidence/knowledge-update/fee-intake.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if len(rows) != 2 or any(job["state"] != "done" for job in jobs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

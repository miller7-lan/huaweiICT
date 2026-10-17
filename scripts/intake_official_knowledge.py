"""Register two narrowly scoped official normative texts and run an audited candidate intake.

Access screening is automated and limited to private engineering storage. It is not a
human business review, a publication approval, or a blanket license for website media.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, encode
from gov_mcp.knowledge_fetch import schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]
COPYRIGHT_BASIS = "https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html"
BASE = "https://www.beijing.gov.cn"
SPECS = [
    {"id": "MPS-NORM-01", "title": "公安部关于印发《户口居民身份证管理工作规范（试行）》的通知",
     "path": "/zhengce/zhengcefagui/202211/t20221116_2860246.html", "document_type": "administrative_norm",
     "issuer": "公安部", "document_number": "公通字〔2021〕12号",
     "initial_page_observations": {"published_at_field": "2021-07-30", "effective_from_field": "2021-10-01", "signed_at_body": "2021-07-24",
         "date_note": "PubDate meta concatenates two dates; URL date is not an effective date; business review pending"},
     "include_articles": ["第一条", "第二条", "第十条", "第十二条", "第七十六条", "第七十八条", "第七十九条", "第八十条", "第八十一条", "第九十六条", "第九十七条"],
     "article_categories": {"第七十六条": "eligibility", "第七十八条": "identity"}},
    {"id": "MPS-USE-01", "title": "关于规范居民身份证使用管理的公告",
     "path": "/zhengce/zhengcefagui/201905/t20190522_59419.html", "document_type": "official_announcement",
     "issuer": "公安部、中央综治办等八部门", "document_number": None,
     "initial_page_observations": {"published_at_field": "2016-08-11", "effective_from_field": "2016-07-15", "signed_at_body": "2016-07-15",
         "date_note": "Body says effective upon publication but page publication differs from signed/effective field; resolve before business publication"}},
]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    stamp = datetime.now(timezone.utc).isoformat()
    basis = ("Official normative/administrative text screened against Copyright Law Article 5(1); "
             "private engineering archive and attributed excerpts only; no website images/layout/news redistribution; "
             "current applicability and business publication remain unapproved. Basis: " + COPYRIGHT_BASIS)
    before = store.status()["counts"]
    for spec in SPECS:
        config = {"id": spec["id"], "label": spec["title"], "origin": BASE,
                  "allowed_paths": [spec["path"]], "seeds": [BASE+spec["path"]], "enabled": True,
                  "interval_seconds": 86400, "delay_seconds": 5, "content_selector": {"id": "mainText"},
                  "metadata_selectors": {"document_fields": {"class": "doc-info"}},
                  "discover_links": False, "page_kind": "document",
                  "metadata": {"title": spec["title"], "region": "national", "matter": "demo-id-renewal",
                      "document_type": spec["document_type"], "issuer": spec["issuer"], "document_number": spec["document_number"],
                      "published_at": None, "effective_from": None, "effective_to": None, "usage_basis": basis,
                      "business_review": "pending", "scope_confirmed": True,
                      "collection_role": "core_candidate", "initial_page_observations": {"recorded_at": stamp, **spec["initial_page_observations"]}},
                  "access_review": {"approved": True, "reviewer": "codex_automated_technical_access_screen", "actor_kind": "machine",
                      "checked_at": stamp, "usage_basis": basis, "business_review_completed": False,
                      "purpose": "private_engineering_candidates_only", "robots_url": BASE+"/robots.txt",
                      "screening_note": "robots and normal GET previously returned 200; rules are rechecked by every fetch; no blanket permission inferred"}}
        for option in ("include_articles", "article_categories"):
            if option in spec:
                config[option] = spec[option]
        store.register(config)
    # Preserve once-observed failures rather than retrying or changing identity to evade them.
    probe_results = {"ID-LAW-01": "robots request denied (AccessStopped); no body requested in this run",
                     "ID-GUIDE-01": "robots request denied (AccessStopped); no body requested in this run",
                     "ID-NORM-01": "robots request timed out; no PDF requested in this run"}
    with store.db() as db:
        for source_id, reason in probe_results.items():
            db.execute("UPDATE sites SET paused_reason=? WHERE id=?", (reason, source_id))
            store.log(db, "source_access_probe_recorded", {"id": source_id, "result": reason, "checked_date": "2026-10-15",
                "provenance": "Observed direct Fetcher probe output earlier in this task; this statement makes no new request"})
    schedule(store)
    for _ in SPECS:
        worker_once(store, owner="official-intake")
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='official-intake'")
    status = store.status()
    with store.db() as db:
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
        jobs = [dict(r) for r in db.execute("SELECT site_id,url,state,attempts,error FROM jobs WHERE site_id IN ('MPS-NORM-01','MPS-USE-01')")]
        revisions = []
        for row in db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id IN ('MPS-NORM-01','MPS-USE-01')"):
            revisions.append({"revision_id": row["id"], "source_id": row["site_id"], "url": row["url"], "sha256": row["sha256"],
                              "state": row["state"], "chunks": db.execute("SELECT COUNT(*) FROM chunks WHERE revision_id=?", (row["id"],)).fetchone()[0]})
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": stamp, "before_counts": before, "after_counts": status["counts"], "jobs": jobs,
              "revisions": revisions, "other_source_probes": probe_results,
              "business_review_completed": False, "official_knowledge_published": 0,
              "scope": "Actual direct official HTML acquisition into pending candidates. No bypass, no business approval, no assertion of current legal applicability."}
    output = ROOT / "evidence/knowledge-update/official-intake-2026-10-15.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(job["state"] == "done" for job in jobs) or len(revisions) < len(SPECS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

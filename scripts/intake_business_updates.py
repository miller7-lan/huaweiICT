"""Register bounded official business-evidence candidates through the normal worker."""

import argparse
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import schedule, worker_once
from gov_mcp.knowledge_lock import process_lock
from nexent_api import ROOT, RUNTIME


TARGETS = {
    "henan-materials-reply": {
        "id": "HN-MATERIALS-REPLY-2023", "origin": "https://www.henan.gov.cn", "path": "/2023/02-04/2683999.html",
        "label": "河南省公安厅公开答复：省内异地补换领材料分支", "selector": {"id": "content"},
        "keywords": ["省内", "居民身份证"], "expected": 3,
        "region": "河南省", "type": "official_consultation_reply", "role": "material_branch_signal",
        "page_date": "2023-02-06", "source_named": "河南省公安厅（河南省政府门户网站公开答复）",
        "basis": "Official provincial police public consultation reply. Limited private integrity archive and attributed factual analysis of the three complete non-personal paragraphs identifying the provincial out-of-home-area branch, its context and alternative identity documents. No unrelated correspondence, images or page layout in retrieval; no present applicability approval.",
        "limits": "Only the provincial out-of-home-area heading and two complete context/material paragraphs. The question concerns lost-document replacement and the reply covers multiple branches. Preserve the original punctuation and the parenthetical limitation on resident ID cards. Do not turn this into an unconditional expiry-renewal alternative list, a current Erqi local checklist or an amendment/repeal of the national/provincial norms. Reply/page date is not a legal commencement date.",
    },
    "erqi-nearby": {
        "id": "ERQI-NEARBY-2026", "origin": "https://zzga.zhengzhou.gov.cn", "path": "/jwzx/9863401.jhtml",
        "label": "二七区警务室就近办：郑州公安2026年服务事实", "selector": {"class": "news_content_content"},
        "keywords": ["据了解，自去年9月份开始", "居民身份证补换领"], "expected": 1,
        "region": "郑州市二七区", "type": "official_service_information", "role": "local_service_signal",
        "page_date": "2026-01-23", "source_named": "郑州市公安局",
        "basis": "Official police service-information page. Limited private integrity archive and attributed factual analysis of the single paragraph describing nearby service coverage. No case narratives, personal details, illustrations or full news republication in retrieval. This is a technical use screen, not an effectivity or office-level acceptance guarantee.",
        "limits": "One complete service-coverage paragraph only. The cases concern lost documents; no inference that all 148 rooms currently accept every expiry-renewal branch or provide identical materials, hours, fees or delivery.",
    },
    "henan-25-days": {
        "id": "HN-MEASURES-2024-JY", "origin": "https://gaj.jiyuan.gov.cn", "path": "/14190/15132/15148/t961601.html",
        "label": "河南2024便民措施：济源公安转载的25自然日条款", "selector": {"class": "view"},
        "keywords": ["一、压缩居民身份证办理时限", "25个自然日"], "expected": 1,
        "region": "河南省", "type": "official_policy_explanation", "role": "later_policy_signal",
        "page_date": "2024-10-17", "source_named": "河南省公安厅（济源公安转载）",
        "basis": "Official police policy-explanation republication naming the provincial police as source. Limited private integrity archive and attributed factual analysis of the one complete clause specifying 25 natural days from acceptance to collection. No unrelated policy sections or media in retrieval; present applicability and subsequent changes remain unapproved.",
        "limits": "Only the complete first measure. Other sections include loss/damage online-service branches and cannot be treated as ordinary expiry-renewal eligibility. Page publication is not the measure's commencement date.",
    },
    "fee-exemption-2004": {
        "id": "NDRC-EXEMPT-2004", "origin": "https://www.ndrc.gov.cn", "path": "/xxgk/zcfb/qt/200905/t20090512_967638.html",
        "label": "财政部国家发展改革委关于第二代居民身份证工本费减免政策的通知", "selector": {"class": "TRS_Editor"},
        "region": "national", "type": "administrative_norm", "role": "fee_exemption_candidate",
        "page_date": "2004-02-06", "source_named": "国家发展改革委网站，财政部、国家发展改革委署名",
        "document_number": "财综〔2004〕8号",
        "basis": "Official administrative normative text on the publishing ministry's public site. Private integrity archive and attributed normative-text analysis under Copyright Law Article 5(1), https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html . No site media/design reuse or present-validity certification. This origin is separately reviewed; the paused zfxxgk.ndrc.gov.cn PDF sources are not resumed or bypassed.",
        "limits": "Historical exemption categories and proof requirements need later-policy and local implementation checks. Do not infer that every person in financial difficulty is exempt, all replacement reasons qualify, or the historical proof is today's sole accepted form. URL directory date is not legal commencement.",
    },
}


def main():
    parser = argparse.ArgumentParser(description="通过正常访问检查接入精确官方候选；不会发布知识")
    parser.add_argument("--source", choices=TARGETS, required=True)
    parser.add_argument("--enable-monitoring", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="只核对现有归档和任务，不联网或重排任务")
    args = parser.parse_args()
    if args.verify_only and args.enable_monitoring:
        parser.error("只读核对不能同时启用监测")
    target = TARGETS[args.source]
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=args.verify_only)
    url = target["origin"] + target["path"]
    stamp = datetime.now(timezone.utc).isoformat()
    if not args.verify_only:
        with process_lock(store.root / "source-intake.lock") as acquired:
            if not acquired:
                raise RuntimeError("另一个来源接入正在运行")
            with store.db() as db:
                if db.execute("SELECT 1 FROM sites WHERE id=?", (target["id"],)).fetchone():
                    raise ValueError("来源已登记；请用正常后台监测或只读核对，不能重复引导")
            config = {
                "id": target["id"], "label": target["label"], "origin": target["origin"],
                "allowed_paths": [target["path"]], "seeds": [url], "enabled": args.enable_monitoring,
                "interval_seconds": 86400, "delay_seconds": 5, "page_kind": "document", "discover_links": False,
                "content_selector": target["selector"],
                "watch_scopes": [{"region": "郑州市二七区", "matter": "demo-id-renewal"}],
                "metadata": {
                    "title": target["label"], "region": target["region"], "matter": "demo-id-renewal",
                    "document_type": target["type"], "collection_role": target["role"], "scope_confirmed": False,
                    "effective_from": None, "effective_to": None, "published_at": None,
                    "business_review": "pending", "usage_basis": target["basis"],
                    "selected_content_scope": target["limits"],
                    "initial_page_observations": {"page_date": target["page_date"], "source_named": target["source_named"],
                                                  "not_a_legal_effective_date": True},
                },
                "access_review": {
                    "approved": True, "actor_kind": "machine", "reviewer": "Codex AI technical access/use screen",
                    "checked_at": stamp, "purpose": "bounded_exact_official_business_evidence_monitoring",
                    "usage_basis": target["basis"], "business_review_completed": False,
                },
            }
            if target.get("document_number"):
                config["metadata"]["document_number"] = target["document_number"]
            if target.get("keywords"):
                config.update(include_paragraph_keywords=target["keywords"], paragraph_expected_count=target["expected"])
            store.register(config)
            if args.enable_monitoring:
                schedule(store)
                worker_once(store, owner="business-evidence-intake")
            with store.db() as db:
                configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
                if not db.execute("SELECT 1 FROM jobs WHERE owner='business-evidence-intake' AND state='running'").fetchone():
                    db.execute("UPDATE workers SET progress='completed' WHERE id='business-evidence-intake'")
            (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    with store.db() as db:
        source = db.execute("SELECT * FROM sites WHERE id=?", (target["id"],)).fetchone()
        if not source:
            raise ValueError("来源尚未登记")
        config = json.loads(source["config"])
        if config["origin"] != target["origin"] or config["seeds"] != [url]:
            raise ValueError("来源边界已变化，请重新核查")
        jobs = [dict(row) for row in db.execute("SELECT id,state,attempts,owner,error FROM jobs WHERE site_id=?", (target["id"],))]
        row = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (target["id"], url)).fetchone()
        archived = None
        if row:
            store._verify(db, row["id"], require_review=False)
            chunks = [dict(item) for item in db.execute("SELECT locator,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],))]
            archived = {"revision": row["id"], "raw_sha256": row["sha256"], "state": row["state"],
                        "chunks": len(chunks), "locators": [item["locator"] for item in chunks],
                        "source_observation": json.loads(row["metadata"]).get("source_observation")}
            (store.root / (target["id"]+"-selected-private.json")).write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        report = {"captured_at": stamp, "source_id": target["id"], "url": url,
                  "read_only_verification": args.verify_only, "network_requests_by_this_verification": 0 if args.verify_only else None,
                  "automatic_monitoring_enabled": config["enabled"], "jobs": jobs, "archive": archived,
                  "source_state": {key: source[key] for key in ("last_success", "paused_reason", "failures")},
                  "body_acquired": bool(archived), "business_review_completed": False, "human_attestations_created": 0,
                  "scope": target["limits"]}
    (ROOT / "evidence/knowledge-update" / (args.source+"-candidate.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

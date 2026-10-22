"""One-time bootstrap of three inspected scans into normally guarded OCR monitors.

Deploy the current worker first. Re-running this bootstrap on already ingested
sources is refused: subsequent checks belong to their durable worker jobs.
"""

import argparse
import json
import uuid
from datetime import datetime, timezone

from pypdf import PdfReader

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import create_bundle
from gov_mcp.knowledge_fetch import ArticleParser, schedule, worker_once
from intake_henan_sources import TARGETS
from nexent_api import ROOT, RUNTIME


SPECS = {
    "measures-2021-pdf": {"sha256": "db257f4c918d95d6212c92262309ddd9a10ef33ec5354e0bbbf954df1ac52eff", "pages": 8,
        "parent": "https://hnga.henan.gov.cn/2021/06-01/2156062.html", "parent_sha256": "07311ee44758195fde18e8739d681ff7cbc7fdfaff18b0e1393849ff4bebd6d1",
        "title": "河南省公安厅关于印发2021年便民利企服务新举措的通知", "number": "豫公〔2021〕16号", "signed_at": "2021-03-25",
        "visual_findings": "AI逐页检查8页可见CropBox：第5页第二项省内通办，第5至6页第三项将受理到领证周期从40自然日压缩至30日；第4页盖章2021-03-25。2026通知只修改第四项旅馆许可，不能推断二、三项因此废止或当前必定适用。未找到六个月调整条款。",
        "expected_tokens": ["省内通办", "40", "30"], "excluded": False},
    "repeal-2020-pdf": {"sha256": "0f30596cce1fd8e22a62c315b368beddf829e462fcbc99067aaa703ea13ee0c2", "pages": 2,
        "parent": "https://hnga.henan.gov.cn/2020/12-29/2069144.html", "parent_sha256": "1031c0d6f8f3ee68a77bc3f361ac64cc9e5d092dd7d9dd15c0881dec164529ae",
        "title": "河南省公安厅关于废止部分行政规范性文件的通知（2020）", "number": "豫公通〔2020〕122号", "signed_at": "2020-12-17",
        "visual_findings": "AI检查2页：废止行政听证、特种行业、气枪、高速交警执法、车辆及驾驶人管理5件文件，未涉及试点身份证换领规范。OCR把2015识别为20巧，盖章日期亦不完整；不将机器日期直接作为效力字段。",
        "expected_tokens": ["1996", "2009"], "excluded": True},
    "repeal-2021-pdf": {"sha256": "c5af193ea4d4cd4a1c150c9177e43d17aeba3a24b36409f5f4ada3fad702502e", "pages": 2,
        "parent": "https://hnga.henan.gov.cn/2021/12-24/2371407.html", "parent_sha256": "e51b2ee99c0370d500cbeaed75a06f2826e191c7ceb0ad4dc4f46b8107d07f11",
        "title": "河南省公安厅关于废止部分行政规范性文件的通知（2021）", "number": "豫公通〔2021〕108号", "signed_at": "2021-12-14",
        "visual_findings": "AI检查2页：废止印章刻字业及安全管理3件、机动车驾驶人考场1件、交通财损事故协商1件，未列试点规范；盖章/印发2021-12-14，页面2021-12-24，不互换日期。OCR有2015和盖章日期识别错误。",
        "expected_tokens": ["印章", "2017"], "excluded": True},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-monitoring", action="store_true", required=True)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    prepared = []
    for key, spec in SPECS.items():
        target = TARGETS[key]
        url = target["origin"]+target["path"]
        raw = (store.root / "intake-2026-10-16" / target["archive"]).read_bytes()
        intake = json.loads((ROOT / "evidence/knowledge-update" / target["report"]).read_text(encoding="utf-8"))
        if fingerprint(raw) != spec["sha256"] or intake["raw_sha256"] != spec["sha256"] or not intake["body_acquired"]:
            raise ValueError("Inspected scan archive differs")
        rendered = sorted((store.root / "intake-2026-10-16" / (target["archive"][:-4]+"-pages")).glob("*.png"))
        if len(rendered) != spec["pages"]:
            raise ValueError("Complete rendered visual-check page set required")
        with store.db() as db:
            if db.execute("SELECT 1 FROM documents WHERE site_id=?", (target["id"],)).fetchone():
                raise ValueError("Scan already ingested; use the ordinary monitor, not the first bootstrap")
            config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (target["id"],)).fetchone()[0])
            parent = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id='HN-POLICY-DISCOVERY' AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (spec["parent"],)).fetchone()
            if not parent or parent["sha256"] != spec["parent_sha256"]:
                raise ValueError("Parent changed since attachment inspection")
            store._verify(db, parent["id"], require_review=False)
        parent_raw = (store.root / "originals" / parent["sha256"][:2] / parent["sha256"]).read_bytes()
        article = ArticleParser({"id": "content"}, [{"class": "operator"}])
        article.feed(parent_raw.decode("utf-8"))
        if url not in article.selected_links or config["enabled"] or config["seeds"] != [url]:
            raise ValueError("Reviewed exact visible attachment link and disabled source required")
        config.update(enabled=True, pdf_ocr={"engine": "windows.media.ocr", "language": "zh-Hans-CN"},
            watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}])
        config["label"] = spec["title"]+"：扫描原件及OCR候选"
        config["metadata"].update(title=spec["title"], region="unknown", matter="unclassified", scope_confirmed=False,
            discovered_from=spec["parent"], attachment_parent_revision=parent["id"], attachment_parent_sha256=parent["sha256"],
            document_number=spec["number"], initial_page_observations={"signed_at_from_ai_visual_check": spec["signed_at"],
                "current_applicability_unresolved": True, "ocr_dates_not_used_as_legal_dates": True})
        config["access_review"]["purpose"] = "exact_official_normative_attachment_daily_monitor_with_pending_native_ocr"
        prepared.append((key, config, rendered))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    create_bundle(store, store.root / "backups" / (stamp+"-before-native-ocr"))
    for _, config, _ in prepared:
        store.register(config)
    schedule(store)
    for _ in range(12):
        if not worker_once(store, owner="henan-scan-bootstrap"):
            break
    records = []
    for key, config, rendered in prepared:
        spec = SPECS[key]
        with store.db() as db:
            row = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (config["id"],)).fetchone()
            jobs = [dict(r) for r in db.execute("SELECT url,state,attempts,error FROM jobs WHERE site_id=?", (config["id"],))]
            if not row or row["sha256"] != spec["sha256"] or any(j["state"] != "done" for j in jobs):
                raise ValueError("Normal monitored acquisition differs or is incomplete; retain pending state")
            revision_row, chunks = store._content(db, row["id"])
            metadata = json.loads(revision_row["metadata"])
        extraction = metadata["source_observation"]["pdf_extraction"]
        text = "\n".join(c["text"] for c in chunks)
        checks = {"actual_windows_ocr": extraction["method"] == "windows_media_ocr",
            "all_pages_retained": len(chunks) == len(extraction["raw_ocr_pages"]) == spec["pages"],
            "inspected_original_hash_matches": row["sha256"] == spec["sha256"],
            "key_tokens_observed": all(token in text for token in spec["expected_tokens"]),
            "pending_ocr_provenance": extraction["requires_visual_review"] and not extraction["confidence_available"],
            "legal_dates_unset": metadata["effective_from"] is None and metadata.get("published_at") is None}
        if not all(checks.values()):
            raise ValueError("OCR/source check failed; do not reuse a scope conclusion")
        revision = row["id"]
        if spec["excluded"]:
            revision = store.exclude(revision, "Codex AI scan source-scope analysis", spec["visual_findings"], confirmed=True)
        # The HTML pointer is not an additional normative instrument. If its
        # link changes later, the ordinary discovery worker creates a new pending revision.
        with store.db() as db:
            parent = db.execute("SELECT r.id,r.sha256 FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.url=? ORDER BY r.rowid DESC LIMIT 1", (spec["parent"],)).fetchone()
        if parent["sha256"] != spec["parent_sha256"]:
            raise ValueError("Parent changed during processing; recheck it before excluding the pointer")
        store.exclude(parent["id"], "Codex AI attachment-provenance analysis", "该HTML只含已取得的PDF入口，不作为额外政策正文引文。PDF已单独保留原件和OCR候选；入口链接变化仍须复核。"+spec["visual_findings"], confirmed=True)
        reader = PdfReader(store.root / "intake-2026-10-16" / TARGETS[key]["archive"])
        records.append({"source_id": config["id"], "url": config["seeds"][0], "revision": revision, "raw_sha256": spec["sha256"],
            "checks": checks, "jobs": jobs, "pages": len(chunks), "page_order": "source CropBox order",
            "page_boxes": [{"page": i+1, "media_box": list(page.mediabox), "crop_box": list(page.cropbox)} for i, page in enumerate(reader.pages)],
            "rendered_page_hashes": {p.name: fingerprint(p.read_bytes()) for p in rendered},
            "ai_visual_findings": spec["visual_findings"], "human_attestation_created": False, "scope_excluded": spec["excluded"]})
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='henan-scan-bootstrap'")
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    status = store.status()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": True, "records": records,
        "counts": status["counts"], "normal_guarded_monitored_sources": 3, "automatic_ocr_pages": sum(r["pages"] for r in records),
        "business_publication_approved": False, "scope": "Three exact publisher-linked scanned PDFs, normal guarded body rechecks and actual local Windows OCR. Candidate extraction and AI visual scope analysis, not independent human business acceptance or current policy guarantee."}
    (ROOT / "evidence/knowledge-update/henan-scan-ocr.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "scans": len(records), "ocr_pages": report["automatic_ocr_pages"], "counts": status["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

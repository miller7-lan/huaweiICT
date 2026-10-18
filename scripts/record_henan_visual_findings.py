"""Archive two manually inspected scans as AI transcriptions, excluded from pilot answers.

No OCR/text-layer claim, human attestation, current-validity decision or automatic
PDF monitoring is manufactured. All 18 rendered pages were inspected on this run.
"""

import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME
from intake_henan_sources import TARGETS


FINDINGS = {
    "validity-pdf": {"sha256": "f6a32e4bf55d2148b498bd00c3451912d1405bc3ca72566dc356e7fca4afbe51", "pages": 16,
        "number": "豫公通〔2019〕141号", "signed_at": "2019-06-16", "locator": "PDF第1页（AI视觉转录）",
        "text": "各省辖市公安局、厅直有关单位：\n按照省政府关于规范性文件管理的有关规定及要求，近期，省公安厅对73件行政规范性文件进行了清理，决定继续有效50件，废止23件，现予以公布。决定废止的行政规范性文件自公布之日起废止。\n附件：1.继续有效的行政规范性文件目录\n2.废止的行政规范性文件目录",
        "parent": "https://hnga.henan.gov.cn/2025/10-21/3237047.html",
        "reason": "AI逐页检查16页扫描件，通知文号和盖章日期为2019年，不是2025年作出的现行效力清理。不能用它核定2022年出台规范在2026年的效力。作为历史沿革留档，排除本试点正式回答；目录页日期与文件日期分别保留。",
        "facts": {"continued_valid_count_at_issue": 50, "repealed_count_at_issue": 23, "listed_items_end_at_year": 2018}},
    "repeal-pdf": {"sha256": "20f76fbc3bfe78bfd205e415879c15a5185ce3d07ad0e20f4114835a5f77f603", "pages": 2,
        "number": "豫公通〔2022〕86号", "signed_at": "2022-08-02", "locator": "PDF第1至2页（AI视觉转录）",
        "text": "各省辖市公安局、济源产城融合示范区公安局：\n依据《河南省行政规范性文件管理办法》（省政府令〔2015〕169号），按照行政规范性文件动态清理和清理兜底要求，经审查研究，省公安厅部分文件已不适应当前全面深化改革和治安、网安等工作需要，现决定对以下文件予以废止，废止文件自即日起不再执行。\n1.河南省公安厅关于印发《河南省公共娱乐服务场所治安监督管理规定》的通知（豫公通〔2002〕465号）\n2.河南省公安厅关于贯彻落实《公安部加强自行车安全管理工作的通知》的通知（豫公通〔2008〕14号）\n3.河南省公安厅关于印发《河南省公安机关网安系统行政执法裁量标准（修订）》（豫公通〔2014〕23号）",
        "parent": "https://hnga.henan.gov.cn/2025/10-21/3237046.html",
        "reason": "AI检查2页扫描件，正文为2022年8月2日的3件文件废止通知，涉及娱乐场所、自行车安全和网安执法裁量，不是2025年新作决定，也未列本试点2022户口居民身份证规范。排除本试点正式回答，仅留沿革证据；不据此反推其他文件全部有效。",
        "facts": {"repealed_document_numbers": ["豫公通〔2002〕465号", "豫公通〔2008〕14号", "豫公通〔2014〕23号"]}},
}

HTML_EXCLUSIONS = {
    "https://hnga.henan.gov.cn/2022/03-18/2417195.html": "已逐段检查20段正文，十项措施涉及新生儿入户、无犯罪记录、保安/金融许可、外国人居留、交通和平台服务，未提供本试点到期换领规则。不能把其他业务时限/材料移作身份证要求。",
    "https://hnga.henan.gov.cn/2025/05-08/3155683.html": "已检查8段全文，废止5件文件涉及特种行业、娱乐场所、民爆、交通代收和交通事故，无本试点规范。排除试点问答，但继续监测该通知页面变化。",
    "https://hnga.henan.gov.cn/2026/01-22/3312600.html": "已检查14段全文，修改保安服务登记及2021便民措施的旅馆业许可项，废止旅馆治安信息系统通知。未修改本试点居民身份证条款；不能因名称泛化判定所有便民措施或身份证规范已废止。",
}
INSPECTED_HTML_SHA256 = {
    "https://hnga.henan.gov.cn/2022/03-18/2417195.html": "a56c6128bafa7fb75e8486ceb6f234f58fcc222056a35d141a9e43e51c6ddfae",
    "https://hnga.henan.gov.cn/2025/05-08/3155683.html": "45aaa93c4aaaf8cfa6bc62284a751b6a4dfcd754e21064f94a56cdd2d9981c9c",
    "https://hnga.henan.gov.cn/2026/01-22/3312600.html": "e10b1eda14a0c3a9a105c40da88eae25a802d7bafc87cd9a948e9b4eea00677b",
    "https://hnga.henan.gov.cn/2025/10-21/3237046.html": "40fb56ed8ca0d01e8af060f698ff1bda046907b6d3db5eac8707d3985558c68f",
    "https://hnga.henan.gov.cn/2025/10-21/3237047.html": "56a9ac6c631485da5700e39f7c000e8c0680966f0203d9d72a9273a06efb0291",
}


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    records = []
    for key, finding in FINDINGS.items():
        target = TARGETS[key]
        intake = json.loads((ROOT / "evidence/knowledge-update" / target["report"]).read_text(encoding="utf-8"))
        raw = (store.root / "intake-2026-10-16" / target["archive"]).read_bytes()
        if fingerprint(raw) != finding["sha256"] or intake["raw_sha256"] != finding["sha256"] or not intake["body_acquired"]:
            raise ValueError("Inspected PDF fingerprint differs")
        with store.db() as db:
            config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (target["id"],)).fetchone()[0])
            existing = db.execute("SELECT r.id,r.sha256,r.state FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (target["id"],)).fetchone()
        if config["enabled"] or config["seeds"] != [intake["url"]]:
            raise ValueError("Expected disabled exact-attachment source required")
        if existing:
            if existing["sha256"] != finding["sha256"] or existing["state"] != "excluded":
                raise ValueError("Existing attachment requires separate review; will not overwrite it")
            revision = existing["id"]
        else:
            config["label"] = target["label"]
            config["metadata"].update(title=target["label"], region="unknown", matter="unclassified", scope_confirmed=False,
                document_number=finding["number"], document_signed_at=finding["signed_at"], collection_role="governance_reference",
                extraction_method="ai_visual_transcription", text_layer_available=False, human_review_completed=False,
                selected_content_scope=finding["locator"]+"；通知正文视觉转录，目录全部项目未转录；原PDF完整保存",
                source_observation={"schema": 1, "media_type": "application/pdf", "raw_sha256": finding["sha256"],
                    "parent_page_observed_date": "2025-10-21", "document_signed_at": finding["signed_at"], "date_mismatch": True},
                discovered_from=finding["parent"])
            store.register(config)
            revision = store.ingest(target["id"], intake["url"], raw, config["metadata"],
                [{"locator": finding["locator"], "category": "procedure", "text": finding["text"]}])
            revision = store.exclude(revision, "Codex AI visual source analysis", finding["reason"], confirmed=True)
        records.append({"source_id": target["id"], "url": intake["url"], "revision": revision,
            "raw_sha256": finding["sha256"], "pages_rendered_and_visually_inspected": list(range(1, finding["pages"]+1)),
            "rendered_page_hashes": {p.name: fingerprint(p.read_bytes()) for p in (store.root / "intake-2026-10-16" / ("validity-pages" if key == "validity-pdf" else "repeal-pages")).glob("*.png")},
            "document_number": finding["number"], "document_signed_at": finding["signed_at"],
            "text_layer_available": False, "transcription_author_kind": "ai", "human_attestation_created": False,
            "finding": finding["reason"], "facts": finding["facts"], "automatic_attachment_monitoring_enabled": False})
    for url, reason in dict(HTML_EXCLUSIONS, **{finding["parent"]: finding["reason"] for finding in FINDINGS.values()}).items():
        with store.db() as db:
            row = db.execute("SELECT r.id,r.state,r.sha256 FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id='HN-POLICY-DISCOVERY' AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (url,)).fetchone()
        if not row or row["sha256"] != INSPECTED_HTML_SHA256[url]:
            raise ValueError("Directory candidate changed since inspection; do not reuse its old scope exclusion")
        if row["state"] != "excluded":
            store.exclude(row["id"], "Codex AI source-scope analysis", reason, confirmed=True)
    with store.db() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "author_kind": "ai", "records": records,
        "html_scope_exclusions": HTML_EXCLUSIONS, "counts": store.status()["counts"], "business_publication_approved": False,
        "note": "Page/storage dates are not document issue dates. Historical exclusions do not certify that every other instrument remains valid. Scans are retained with explicit AI visual transcription provenance, not automatic OCR or human approval."}
    (ROOT / "evidence/knowledge-update/henan-visual-source-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"scan_archives": len(records), "pages_visually_inspected": sum(len(r["pages_rendered_and_visually_inspected"]) for r in records),
        "html_candidates_excluded_from_pilot": 5, "counts": report["counts"], "human_attestations_created": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

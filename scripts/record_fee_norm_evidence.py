"""Retain three inspected official corroborating originals, outside pilot answers.

This one-time, fingerprint-bound intake uses earlier normal Fetcher receipts. It
does not add a general spreadsheet parser or a second monitoring mechanism.
"""

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import IndexLinks, allowed_url, validate_config
from gov_mcp.knowledge_pdf import extract_document
from nexent_api import ROOT, RUNTIME


SPECS = {
    "HN-NORM-XICHUAN-PDF": {
        "url": "https://www.xichuan.gov.cn/ifile/20251229/1766973939411PxhOsju6.pdf",
        "file": "henan-norm.pdf", "sha256": "fbb7d6383dc63131fa70cbacac85b6585cb9827a15c806533abf22176d1f5b9c",
        "probe": "HN-NORM-XICHUAN-PROBE", "receipt": "probe-results.json",
        "label": "河南户口居民身份证规范2022通知原件：淅川政府公开副本",
        "reason": "保留文件身份及第159、236条对照证据。已有省厅正文来源承担监测；本副本只选3页，不自动采集64页户口业务，不据官方副本存在核定2026年现行效力。",
    },
    "HN-FEE-CATALOG-PAGE-2025": {
        "url": "https://czt.henan.gov.cn/2025/12-30/3280093.html",
        "file": "fee-dec2025.html", "sha256": "a74116dd13b8a1f88fc51c6bc59563eb1790f0d2e47d5cd350845a08024b5ab5",
        "probe": "HN-FEE-DEC2025-PROBE", "receipt": "fee-dec2025-result.json",
        "label": "河南财政厅行政事业性收费目录附件入口",
        "reason": "目录附件来源链留档。页面显示日期、元数据与URL目录日期分别保留，不把目录更新当作收费政策施行日；不将整站目录或附件标题作为业务答案。",
    },
    "HN-FEE-CATALOG-XLSX-2025": {
        "url": "https://xcoss.henan.gov.cn/typtfile/20250318/fdace0fca875409fa1338179dd218530.xlsx",
        "file": "henan-fee-catalog.xlsx", "sha256": "4aaddf9c385df36a3c53bd964b803a5c6d7ecca5d186cb8aee96a0e7d6e9562c",
        "probe": "HN-FEE-WORKBOOK-PROBE", "receipt": "fee-workbook-result.json",
        "label": "河南财政厅目录附件：居民身份证收费行及表头",
        "reason": "仅保留目录标题、公安部门及收费行相关表头和原单元格值。收费标准列列文号而非金额；不能据此虚构20元单元格、目录有效期、现行减免证明或二七区额外收费。普通采集器尚未配置Excel自动解析，本附件不启用监测。",
    },
}
SHEET_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CELL_SELECTION = ("A1", "D4", "E4", "F4", "G4", "B17", "D36", "E36", "F36", "G36")


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def workbook_cells(path):
    """Read exact selected literal string cells of this pinned, inspected XLSX."""
    with ZipFile(path) as z:
        if any(i.file_size > 5 * 1024 * 1024 for i in z.infolist()) or sum(i.file_size for i in z.infolist()) > 20 * 1024 * 1024:
            raise ValueError("Unexpected oversized workbook members")
        book = ET.fromstring(z.read("xl/workbook.xml"))
        sheet = book.find("s:sheets/s:sheet", SHEET_NS)
        relationship = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        links = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(e.attrib["Target"] for e in links if e.attrib["Id"] == relationship)
        if sheet.attrib["name"] != "行政事业性收费" or target not in ("worksheets/sheet1.xml", "/xl/worksheets/sheet1.xml"):
            raise ValueError("Inspected worksheet relationship changed")
        strings = ["".join(n.itertext()) for n in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", SHEET_NS)]
        values = {}
        for cell in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(".//s:c", SHEET_NS):
            if cell.attrib["r"] not in CELL_SELECTION:
                continue
            if cell.attrib.get("t") != "s" or cell.find("s:f", SHEET_NS) is not None:
                raise ValueError("Expected original literal string cell")
            values[cell.attrib["r"]] = strings[int(cell.find("s:v", SHEET_NS).text)]
    if set(values) != set(CELL_SELECTION):
        raise ValueError("Missing inspected cells")
    return [{"locator": "Excel工作表行政事业性收费!"+cell, "category": "fee", "text": values[cell]} for cell in CELL_SELECTION]


def selected_chunks(source_id, path):
    if source_id == "HN-NORM-XICHUAN-PDF":
        parsed = extract_document(path)
        if len(parsed["chunks"]) != 64:
            raise ValueError("Inspected 64-page document required")
        return [parsed["chunks"][i-1] for i in (1, 45, 64)]
    if source_id == "HN-FEE-CATALOG-XLSX-2025":
        return workbook_cells(path)
    parser = IndexLinks()
    parser.feed(path.read_text(encoding="utf-8"))
    selected = [title for href, title in parser.links if href == SPECS["HN-FEE-CATALOG-XLSX-2025"]["url"]]
    if selected != ["河南省执行的全国性中央部门和单位行政事业性收费目录清单.xlsx"]:
        raise ValueError("Inspected directory attachment changed")
    return [{"locator": "目录正文附件链接标题", "category": "fee", "text": selected[0]}]


def anchor(db, source_id, revision, ordinal):
    row = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (revision, ordinal)).fetchone()
    return {"source_id": source_id, "revision": revision, "chunk_ordinal": ordinal, "locator": row["locator"],
            "start": 0, "end": len(row["text"]), "text_sha256": fingerprint(row["text"].encode()),
            "span_sha256": fingerprint(row["text"].encode())}


def main():
    parser = argparse.ArgumentParser(description="保存已正常取得并检查的官方对照原件；不发布知识")
    parser.add_argument("--research-directory", type=Path, required=True)
    args = parser.parse_args()
    research = args.research_directory.resolve()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    probe = KnowledgeStore(research / "probe", read_only=True)
    stamp = datetime.now(timezone.utc).isoformat()
    records = {}
    for source_id, spec in SPECS.items():
        path = research / spec["file"]
        raw = path.read_bytes()
        receipt = json.loads((research / spec["receipt"]).read_text(encoding="utf-8"))
        if isinstance(receipt, list):
            receipt = next(r for r in receipt if r["source_id"] == spec["probe"])
        with probe.db() as db:
            config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (spec["probe"],)).fetchone()[0])
        validate_config(config)
        allowed_url(config, spec["url"])
        if (fingerprint(raw) != spec["sha256"] or receipt.get("sha256") != spec["sha256"]
                or receipt.get("status", receipt.get("http_status")) != 200 or receipt["url"] != spec["url"]
                or receipt["final_url"] != spec["url"] or config["seeds"] != [spec["url"]]):
            raise ValueError("Normal acquisition receipt or inspected original differs")
        chunks = selected_chunks(source_id, path)
        with store.db() as db:
            current = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (source_id,)).fetchone()
        if current:
            if current["sha256"] != spec["sha256"] or current["state"] != "excluded":
                raise ValueError("Existing reference changed; separate review required")
            revision = current["id"]
        else:
            config.update(id=source_id, label=spec["label"], enabled=False, discover_links=False, page_kind="document")
            metadata = {"title": spec["label"], "region": "unknown", "matter": "unclassified",
                "document_type": "official_corroborating_reference", "collection_role": "governance_reference",
                "effective_from": None, "effective_to": None, "published_at": None, "scope_confirmed": False,
                "business_review": "not_a_pilot_release", "human_review_completed": False,
                "usage_basis": config["metadata"]["usage_basis"], "selected_content_scope": spec["reason"],
                "source_observation": {"schema": 1, "original_sha256": spec["sha256"], "normal_fetcher_receipt": True}}
            if source_id == "HN-NORM-XICHUAN-PDF":
                metadata.update(document_number="豫公通〔2022〕77号", document_signed_at="2022-07-27",
                    extraction_method="pdf_text_layer_selected_complete_pages_1_45_64",
                    selected_pages_visually_checked_by="Codex AI", pages_not_visually_checked=61)
                metadata["source_observation"]["document_stated_start"] = "2022-09-01"
            elif source_id == "HN-FEE-CATALOG-XLSX-2025":
                metadata.update(extraction_method="exact_selected_literal_xlsx_cells_no_formula_evaluation", discovered_from=SPECS["HN-FEE-CATALOG-PAGE-2025"]["url"])
            else:
                metadata.update(extraction_method="exact_official_attachment_link_title")
                metadata["source_observation"].update(displayed_page_date="2025-12-01", meta_pubdate="2025-12-01 17:06:05.0", meta_lastmodified="2025-12-30 17:07", url_directory_date="2025-12-30")
            config["metadata"] = metadata
            store.register(config)
            revision = store.ingest(source_id, spec["url"], raw, metadata, chunks)
            revision = store.exclude(revision, "Codex AI source identity and fee-reference analysis", spec["reason"], confirmed=True)
        store.propose_classification(revision)
        records[source_id] = {"revision": revision, "original_sha256": spec["sha256"], "url": spec["url"],
            "selected_chunks": len(chunks), "state": "excluded", "automatic_monitoring_enabled": False,
            "normal_fetcher_acquisition": True, "scope": spec["reason"]}
    with store.db() as db:
        for source_id in ("MOF-FEE-2007", "MOF-FIRST-ISSUE-2018", "HN-NORM-PUBLISHER"):
            row = db.execute("SELECT r.*,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (source_id,)).fetchone()
            store._verify(db, row["id"], require_review=False)
            records[source_id] = {"revision": row["id"], "original_sha256": row["sha256"], "url": row["url"], "state": row["state"]}
        norm_id = records["HN-NORM-XICHUAN-PDF"]["revision"]
        norm_text = db.execute("SELECT text FROM chunks WHERE revision_id=? AND ordinal=1", (norm_id,)).fetchone()[0]
        original_159 = re.search(r"第一百五十九条.*?(?=第一百六十条)", norm_text, re.S).group()
        publisher_159 = db.execute("SELECT text FROM chunks WHERE revision_id=? AND locator='第一百五十九条'", (records["HN-NORM-PUBLISHER"]["revision"],)).fetchone()[0]
        if compact(original_159) != compact(publisher_159):
            raise ValueError("Article 159 text differs after NFKC and whitespace comparison")
        facts = [
            ("norm_identity", "原件首页文号豫公通〔2022〕77号，盖章日期2022-07-27；不以2025路径判断发文时间。", [("HN-NORM-XICHUAN-PDF", 0)]),
            ("renewal_branch_corroborated", "原件第45页完整第159条与省厅HTML第159条经NFKC及去空白对照一致，未修改任何原文。", [("HN-NORM-XICHUAN-PDF", 1), ("HN-NORM-PUBLISHER", 5)]),
            ("commencement_not_current_validity", "原件末页载2022-09-01施行及2022-07-27印发；未取得现行效力或延期依据，不能设定两年失效。", [("HN-NORM-XICHUAN-PDF", 2)]),
            ("directory_attachment_chain", "河南财政厅目录入口直接链接本Excel原件；显示日和元数据/URL日期不一致，目录无另行核定施行日。", [("HN-FEE-CATALOG-PAGE-2025", 0)]),
            ("directory_fee_references", "目录身份证行F36/G36引用财综〔2007〕34号和财税〔2018〕37号；G36没有金额。", [("HN-FEE-CATALOG-XLSX-2025", 6), ("HN-FEE-CATALOG-XLSX-2025", 8), ("HN-FEE-CATALOG-XLSX-2025", 9)]),
            ("historical_fee_branches", "2007通知第一条分别载换领20、遗失补领/损坏换领40、临时证10元；其中首次申领收费须另读2018条款。", [("MOF-FEE-2007", 1)]),
            ("first_issue_boundary", "2018第一条自2018-04-01停征首次申领工本费，不能扩大为到期换领、遗失补领或损坏换领免费。", [("MOF-FIRST-ISSUE-2018", 0)]),
            ("extra_charge_limits", "2007通知禁止违规收费；自愿邮寄服务与该通知用语及后来措施的关系仍须分别核查，未宣称所有邮费均免费或均应收取。", [("MOF-FEE-2007", 1), ("MOF-FEE-2007", 2), ("MOF-FEE-2007", 3)]),
        ]
        findings = [{"id": key, "finding": text, "business_approval": False,
            "anchors": [anchor(db, source, records[source]["revision"], ordinal) for source, ordinal in refs]} for key, text, refs in facts]
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    images = {str(n): fingerprint((research / f"norm-page-{n}.png").read_bytes()) for n in (1, 45, 64)}
    assessment = {"schema": 1, "captured_at": stamp, "reviewer_kind": "ai", "reviewer": "Codex AI",
        "human_review_completed": False, "business_release_ready": False, "current_erqi_applicability": "not_established",
        "sources": records, "findings": findings, "norm_pdf": {"pages": 64, "pages_visually_inspected": [1,45,64],
            "rendered_page_sha256": images, "original_not_edited": True, "text_layer_artifacts_not_repaired": True},
        "first_2018_tax_site_access": {"url": "https://szs.mof.gov.cn/zhengcefabu/201804/t20180416_2868702.htm",
            "stopped": "robots redirected outside approved origin to http://www.mof.gov.cn/404.htm; body not acquired, no policy bypass", "gazette_source_separately_checked": True},
        "remaining": ["现行二七区材料组合及材料关系", "河南试行规范现行效力及六个月调整依据", "当前减免证明及自愿邮费规则", "独立30题评测与正式两端发布验收"],
        "policy_file_sha256": fingerprint((store.root / "publication-policy.json").read_bytes()),
        "preserved_development_files": json.loads((ROOT / "evaluation/knowledge/material-branch-assessments.json").read_text(encoding="utf-8"))["preserved_development_files"]}
    assessment["assessment_sha256"] = fingerprint(assessment)
    output = ROOT / "evaluation/knowledge/fee-norm-identity-assessments.json"
    output.write_text(json.dumps(assessment, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"reference_originals": len(SPECS), "findings": len(findings), "counts": store.status()["counts"], "business_release_ready": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

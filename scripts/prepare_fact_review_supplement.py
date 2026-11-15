"""Offline, read-only source inspection for the fixed fact-card review sample.

Copy pinned originals and supply missing cell/header/merge and metadata context.
Never modify cards, original evidence, ratings, or the workbook. No network/model.
"""
import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
# Read literal source specifications without importing the builder's HTTP stack.
_tree = ast.parse((ROOT / "scripts/build_frozen_corpus.py").read_text("utf-8"))
_specs = {target.id: ast.literal_eval(node.value) for node in _tree.body if isinstance(node, ast.Assign)
          for target in node.targets if isinstance(target, ast.Name) and target.id in ("SOURCES", "NS")}
SOURCES, NS = _specs["SOURCES"], _specs["NS"]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def workbook(path):
    result = {}
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(n.itertext()) for n in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        links = {n.get("Id"): n.get("Target").lstrip("/").removeprefix("xl/")
                 for n in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            xmlpath = "xl/" + links[sheet.get("{%s}id" % NS["r"])]
            xml = ET.fromstring(archive.read(xmlpath))
            cells = {}
            for cell in xml.findall(".//s:c", NS):
                val = cell.find("s:v", NS)
                if cell.get("t") == "s" and val is not None:
                    text = strings[int(val.text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("s:is", NS).itertext())
                else:
                    text = val.text if val is not None and val.text else ""
                cells[cell.get("r")] = {"text": text, "formula": cell.find("s:f", NS) is not None}
            result[sheet.get("name")] = {"xml": xmlpath, "cells": cells,
                "merges": [n.get("ref") for n in xml.findall("s:mergeCells/s:mergeCell", NS)]}
    return result


def row_cells(sheet, row):
    return [(address, value) for address, value in sheet["cells"].items()
            if int(re.search(r"\d+$", address)[0]) == row]


def table(rows):
    def esc(text):
        return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
    return "| 单元格 | 原始值 | 含公式 |\n| --- | --- | --- |\n" + "".join(
        f"| {a} | {esc(v['text']) or '（空）'} | {'是' if v['formula'] else '否'} |\n" for a, v in rows) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    private = ROOT / ".runtime/dual-track-improvement-202611"
    output = private / "source-supplement"
    if output.exists():
        raise ValueError("EXISTING_SUPPLEMENT_PRESERVED")
    corpus = json.loads(args.corpus.read_text("utf-8"))
    sample = json.loads((private / "sample.json").read_text("utf-8"))
    units = {(u["source_id"], u["locator"]): u for u in corpus["units"]}
    sources = {s["source_id"]: s for s in corpus["manifest"]["sources"]}
    originals = {}
    # Resolve and verify every original before creating any output.
    for spec in SOURCES:
        source = sources[spec["source_id"]]
        candidates = [args.source_root / ".runtime/frozen-qa-20261030/originals" / source["original_sha256"]]
        if spec.get("prepared"):
            candidates.append(args.source_root / ".runtime/multimodal-preparation-20261021" / spec["prepared"])
        if spec.get("private_copy"):
            candidates.append(args.source_root / spec["private_copy"])
        found = next((p for p in candidates if p.is_file() and digest(p) == source["original_sha256"]), None)
        if found is None:
            raise ValueError("PINNED_ORIGINAL_MISSING:" + spec["source_id"])
        originals[spec["source_id"]] = (found, {"xlsx": ".xlsx", "scan": ".pdf", "html": ".html"}[spec["kind"]])
    ledger = ROOT / "docs/competition/dual-track-improvement-202611/human-review.csv"
    ledger_before, sample_before = digest(ledger), digest(private / "sample.json")
    output.mkdir()
    lines = ["# 审核材料补充：原件、Excel结构与元数据\n\n",
             "本文件补充此前漏发的审核依据。原卡片、原阅读材料及既有评分均不改变。"
             "原件字节仅与冻结SHA-256作机械核对，不表示官网当前一致、现行有效或已由人核验。"
             "第19—63项的本次反馈标为审核建议，尚未进入正式判定账本。\n\n",
             "## 归档原件\n\n"]
    receipt = {"network_calls": 0, "model_calls": 0, "originals": [], "cell_items": []}
    for source_id, (path, suffix) in originals.items():
        target = output / (source_id + suffix)
        shutil.copyfile(path, target)
        assert digest(target) == sources[source_id]["original_sha256"]
        lines.append(f"- [{source_id}{suffix}]({target.name})：SHA-256 `{digest(target)}`\n")
        receipt["originals"].append({"source_id": source_id, "file": target.name, "sha256": digest(target)})
    lines.append("\n## 第34项扫描原件与目录信息\n\n"
                 "原目录登记标题仍是未人工核验的目录信息，不能以字节散列通过替代标题语义确认。"
                 "PDF完整原件已附；请在原件上独立判断标题与年份。\n\n")
    catalogue = json.loads((ROOT / "data/knowledge/candidate-catalog.json").read_text("utf-8"))
    scan_doc = next(d for d in catalogue["documents"] if d["source_id"] == "HN-MEASURES-2021-PDF")
    lines.append("```json\n" + json.dumps({k: scan_doc[k] for k in ("source_id", "title", "original_sha256") if k in scan_doc}, ensure_ascii=False, indent=2) + "\n```\n\n")
    observations = json.loads((ROOT / "evidence/competition-multimodal-20261021/scan-visual-observation.json").read_text("utf-8"))
    for image in observations["rendered_pages"]:
        path = args.source_root / f".runtime/multimodal-preparation-20261021/scan-{image['pdf_page']}.png"
        if path.is_file() and digest(path) == image["image_sha256"]:
            shutil.copyfile(path, output / path.name)
            lines.append(f"[历史PDF第{image['pdf_page']}页渲染图]({path.name})（不是新OCR或人工核验）\n\n")
    lines.append("## 第24、25、30项归档HTML的原始meta标签\n\n"
                 "以下为归档HTML标签原文；它只能证明归档文件内的元数据，不能证明发布日期法律意义或当前网页状态。\n\n")
    for number in (24, 25, 30):
        card = sample[number - 1]
        source_id = card["source_id"]
        path, _ = originals[source_id]
        raw = path.read_bytes()
        charset = re.search(br'charset=["\s]*([\w-]+)', raw[:6000], re.I)
        encoding = charset[1].decode("ascii") if charset else "utf-8"
        text = raw.decode(encoding)
        name = "lastmodifiedtime" if number in (24, 30) else "PubDate"
        tags = [tag for tag in re.findall(r"<meta\b[^>]*>", text, re.I) if re.search(r'["\s]' + name + r'["\s]', tag, re.I)]
        lines.append(f"### 第{number}项 · {source_id}\n\n```html\n" + "\n".join(tags) + "\n```\n\n")
        assert tags, (source_id, name)
    sheets = workbook(originals["HN-FEE-CATALOG-XLSX-2025"][0])
    lines.append("## Excel表头与原始结构\n\n"
                 "直接读取原XLSX的OOXML坐标和值，不改文件、不计算公式、不向空格继承值。"
                 "下列字符区间仍对应冻结行文本，原始单元格值及合并范围另列；二者不得混为同一种定位。\n\n")
    for name, sheet in sheets.items():
        lines.append(f"### 工作表：{name}\n\nXML：`{sheet['xml']}`\n\n")
        for row in range(1, 6):
            lines.append(f"表头/前置第{row}行：\n\n" + table(row_cells(sheet, row)))
    for number, card in enumerate(sample, 1):
        if card["source_id"] != "HN-FEE-CATALOG-XLSX-2025":
            continue
        match = re.fullmatch(r"Excel工作表(.+)!第(\d+)行", card["locator"])
        name, row = match[1], int(match[2])
        sheet = sheets[name]
        unit = units[card["source_id"], card["locator"]]
        rawcells = [(a, v) for a, v in row_cells(sheet, row) if v["text"].strip() and not v["formula"]]
        normalized = [(a, re.sub(r"\s+", " ", v["text"]).strip()) for a, v in rawcells]
        assert [a for a, _ in normalized] == unit["cells"]
        assert " | ".join(v for _, v in normalized) == unit["text"]
        spans, offset = [], 0
        for address, text in normalized:
            spans.append({"cell": address, "span": [offset, offset + len(text)], "text": text})
            offset += len(text) + 3
        lines.append(f"### 第{number}项 · {name}!第{row}行\n\n"
                     f"原卡片值：`{json.dumps(card['value'], ensure_ascii=False)}`；原字符区间：`{card['span']}`。\n\n"
                     "本行及前后两行原始单元格（空格不自动补全）：\n\n")
        for nearby in range(max(1, row - 2), row + 3):
            lines.append(f"第{nearby}行：\n\n" + table(row_cells(sheet, nearby)))
        merges = [m for m in sheet["merges"] if int(re.search(r"\d+", m.split(":")[0])[0]) <= row <= int(re.search(r"\d+", m.split(":")[-1])[0])]
        lines.append("跨过本行的合并范围：`" + ", ".join(merges) + "`\n\n" if merges else "跨过本行的合并范围：无。\n\n")
        for merged in merges:
            top = merged.split(":")[0]
            lines.append("合并区域左上角原值：\n\n" + table([(top, sheet["cells"].get(top, {"text": "", "formula": False}))]))
        lines.append("单元格在冻结展开行中的字符区间（用来分清同文号重复出现的位置）：\n\n```json\n" + json.dumps(spans, ensure_ascii=False, indent=2) + "\n```\n\n")
        receipt["cell_items"].append({"number": number, "sheet": name, "row": row, "row_matches_frozen_corpus": True})
    lines.append("## 文号key的设计说明\n\n"
                 "原始number保留完整文号，现有key函数有意只取发文机构尾部两个字再加年份和序号，原用途为宽松匹配辅助。"
                 "它不是完整发文字号，也不适合直接当无碰撞的文档唯一ID。此前未随审核材料解释这一点，现补充说明；"
                 "不据此自动改变本人按完整文号要求提出的判定。\n")
    (output / "README.md").write_text("".join(lines), "utf-8")
    assert digest(ledger) == ledger_before and digest(private / "sample.json") == sample_before
    receipt.update(ledger_unchanged=True, sample_unchanged=True, readme_sha256=digest(output / "README.md"))
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"output": str(output / "README.md"), "originals": len(originals),
                      "xlsx_items": len(receipt["cell_items"]), "ratings_changed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Build the frozen Henan question-answering corpus from seven pinned originals.

Originals are reused from the private preparation copy when present, otherwise
fetched once from their public URL after a robots check. Every original must
match the catalog SHA-256 or that source stops. Units keep exact text and
locators; catalogued clause hashes are re-checked to detect segmentation drift.
Unit text stays in the private runtime; only counts and hashes become evidence.
No model call is made.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from gov_mcp.knowledge_fetch import parse_html  # noqa: E402


RUNTIME = ROOT / ".runtime/frozen-qa-20261030"
EVIDENCE = ROOT / "evidence/frozen-qa-20261030"
CATALOG = ROOT / "data/knowledge/candidate-catalog.json"
PREPARED = ROOT / ".runtime/multimodal-preparation-20261021"
SCAN = ROOT / "evidence/competition-multimodal-20261021/scan-visual-observation.json"
USER_AGENT = "Mozilla/5.0 (frozen-corpus; HuaweiICT topic3 evaluation)"
# Same grouping as knowledge_fetch.select_articles: an article keeps its
# enumerated continuation lines; chapter/section headings end the article.
ARTICLE = re.compile(r"^(第[一二三四五六七八九十百零〇ー]+条)\s*")
HEADING = re.compile(r"^第[一二三四五六七八九十百零〇]+[章节]")
SOURCES = (
    {"code": "A2", "source_id": "HN-FEE-CATALOG-XLSX-2025", "kind": "xlsx",
     "prepared": "HN-FEE-CATALOG-XLSX-2025.xlsx"},
    {"code": "A3", "source_id": "HN-MEASURES-2021-PDF", "kind": "scan",
     "prepared": "HN-MEASURES-2021-PDF.pdf"},
    {"code": "A4", "source_id": "MOF-FEE-2007", "kind": "html", "prepared": "MOF-FEE-2007.html",
     "selector": {"class": "my_doccontent"}},
    {"code": "A5", "source_id": "MOF-FIRST-ISSUE-2018", "kind": "html", "selector": {"class": "my_doccontent"}},
    {"code": "A6", "source_id": "HN-MEASURES-2026", "kind": "html", "selector": {"id": "content"}},
    {"code": "A7", "source_id": "HN-NORM-PUBLISHER", "kind": "html", "selector": {"id": "content"},
     "excluded": [{"class": "operator"}], "min_text_length": 1, "articles": True},
    # The public site currently fails TLS handshakes; the update campaign kept the pinned bytes.
    {"code": "B1", "source_id": "HN-MEASURES-2024-JY", "kind": "html", "selector": {"class": "view"},
     "private_copy": ".runtime/agent-autonomy-repair-20261020-a6/sources/old.html"},
)
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def digest(data):
    return sha256(data).hexdigest()


def catalog_documents():
    documents = json.loads(CATALOG.read_text("utf-8"))["documents"]
    return {document["source_id"]: document for document in documents}


def robots_allowed(client, url):
    parts = urlsplit(url)
    response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    if response.status_code in (401, 403):
        return False
    if response.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def original(spec, document, client, receipts):
    """Return pinned bytes, preferring an existing private copy over the network."""
    expected = document["original_sha256"]
    candidates = [RUNTIME / "originals" / expected]
    if spec.get("prepared"):
        candidates.append(PREPARED / spec["prepared"])
    if spec.get("private_copy"):
        candidates.append(ROOT / spec["private_copy"])
    for path in candidates:
        if path.is_file() and digest(path.read_bytes()) == expected:
            receipts.append({"source_id": spec["source_id"], "origin": "private_copy", "http_requests": 0})
            return path.read_bytes()
    if spec["kind"] == "scan":
        # The scan contributes only its attributed transcription; the PDF is optional here.
        receipts.append({"source_id": spec["source_id"], "origin": "not_needed_for_transcription", "http_requests": 0})
        return None
    if client is None:
        raise RuntimeError("ORIGINAL_MISSING_OFFLINE: " + spec["source_id"])
    if not robots_allowed(client, document["url"]):
        raise RuntimeError("ROBOTS_DISALLOWED: " + spec["source_id"])
    response = client.get(document["url"])
    receipts.append({"source_id": spec["source_id"], "origin": "public_url", "http_requests": 2,
                     "status": response.status_code})
    if response.status_code != 200 or digest(response.content) != expected:
        raise RuntimeError("ORIGINAL_HASH_MISMATCH: " + spec["source_id"])
    target = RUNTIME / "originals" / expected
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return response.content


def unit(spec, locator, text, kind, **extra):
    return {"source_id": spec["source_id"], "code": spec["code"], "locator": locator, "text": text,
            "text_sha256": digest(text.encode()), "evidence_kind": kind, **extra}


def html_units(spec, raw):
    chunks, _ = parse_html(raw, spec["selector"], min_text_length=spec.get("min_text_length", 8),
                           excluded_selectors=spec.get("excluded"))
    if not spec.get("articles"):
        return [unit(spec, chunk["locator"], chunk["text"], "html_paragraph", paragraph=chunk["locator"])
                for chunk in chunks]
    grouped, current = [], None
    for chunk in chunks:
        match = ARTICLE.match(chunk["text"])
        if match:
            current = {"locator": match.group(1), "text": chunk["text"], "paragraph": chunk["locator"]}
            grouped.append(current)
            continue
        if current is not None and not HEADING.match(chunk["text"]):
            current["text"] += "\n" + chunk["text"]
            continue
        current = None
        grouped.append({"locator": chunk["locator"], "text": chunk["text"], "paragraph": chunk["locator"]})
    return [unit(spec, item["locator"], item["text"], "html_article" if ARTICLE.match(item["text"])
                 else "html_paragraph", paragraph=item["paragraph"]) for item in grouped]


def xlsx_units(spec, raw):
    """Literal cell values grouped by row; formulas are skipped and counted, never evaluated."""
    with ZipFile(BytesIO(raw)) as archive:
        if sum(member.file_size for member in archive.infolist()) > 20 * 1024 * 1024:
            raise RuntimeError("WORKBOOK_TOO_LARGE")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(node.itertext()) for node in
                       ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        links = {node.get("Id"): node.get("Target").lstrip("/").removeprefix("xl/")
                 for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        units, formulas = [], 0
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            name = sheet.get("name")
            rows = {}
            for cell in ET.fromstring(archive.read("xl/" + links[sheet.get("{%s}id" % NS["r"])])).findall(".//s:c", NS):
                if cell.find("s:f", NS) is not None:
                    formulas += 1
                    continue
                value = cell.find("s:v", NS)
                if cell.get("t") == "s" and value is not None:
                    text = strings[int(value.text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("s:is", NS).itertext())
                elif value is not None:
                    text = value.text
                else:
                    continue
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    row = int(re.sub(r"[A-Z]+", "", cell.get("r")))
                    rows.setdefault(row, []).append((cell.get("r"), text))
            for row, cells in sorted(rows.items()):
                units.append(unit(spec, f"Excel工作表{name}!第{row}行", " | ".join(text for _, text in cells),
                                  "xlsx_row", cells=[address for address, _ in cells]))
    return units, formulas


def scan_units(spec):
    observation = json.loads(SCAN.read_text("utf-8"))
    if observation["source_id"] != spec["source_id"]:
        raise RuntimeError("SCAN_OBSERVATION_SOURCE_CHANGED")
    return [unit(spec, item["locator"], item["heading"] + "\n" + item["transcription"],
                 "attributed_ai_transcription", human_review_completed=False)
            for item in observation["observations"]]


def check_catalog(units, document):
    """Each catalogued clause must exist with the same text hash, or segmentation drifted."""
    by_locator = {}
    for item in units:
        by_locator.setdefault(item["locator"], []).append(item["text_sha256"])
    checked = 0
    for clause in document.get("selected_clauses") or []:
        hashes = by_locator.get(clause["locator"], [])
        if clause["text_sha256"] not in hashes:
            raise RuntimeError(f"CATALOG_CLAUSE_DRIFT: {document['source_id']} {clause['locator']}")
        checked += 1
    return checked


def build(offline=False):
    documents = catalog_documents()
    receipts, corpus, sources = [], [], []
    client = None if offline else httpx.Client(trust_env=True, timeout=30, follow_redirects=False,
                                               headers={"User-Agent": USER_AGENT})
    try:
        for spec in SOURCES:
            document = documents[spec["source_id"]]
            raw = original(spec, document, client, receipts)
            formulas = 0
            if spec["kind"] == "html":
                units = html_units(spec, raw)
            elif spec["kind"] == "xlsx":
                units, formulas = xlsx_units(spec, raw)
            else:
                units = scan_units(spec)
            checked = check_catalog(units, document) if spec["kind"] == "html" else 0
            corpus.extend(units)
            sources.append({"code": spec["code"], "source_id": spec["source_id"], "kind": spec["kind"],
                            "url": document["url"], "revision": document["revision"],
                            "original_sha256": document["original_sha256"], "units": len(units),
                            "catalog_clauses_rechecked": checked, "formula_cells_skipped": formulas,
                            "units_sha256": digest(json.dumps(units, ensure_ascii=False, sort_keys=True).encode())})
    finally:
        if client is not None:
            client.close()
    manifest = {"schema_version": 1, "corpus": "henan-frozen-qa-20261030",
                "built_at": datetime.now(timezone.utc).isoformat(), "sources": sources,
                "unit_count": len(corpus), "acquisition": receipts, "model_calls": 0,
                "corpus_sha256": digest(json.dumps(corpus, ensure_ascii=False, sort_keys=True).encode()),
                "scope_note": "Frozen archived sources for evaluation; not current policy. "
                              "Only the scan's two attributed AI transcriptions are included."}
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "corpus.json").write_text(json.dumps({"manifest": manifest, "units": corpus},
                                                    ensure_ascii=False, indent=1), "utf-8")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "corpus-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use private copies only; never fetch")
    args = parser.parse_args()
    manifest = build(offline=args.offline)
    print(json.dumps({"unit_count": manifest["unit_count"], "corpus_sha256": manifest["corpus_sha256"],
                      "sources": [(s["code"], s["units"], s["catalog_clauses_rechecked"]) for s in manifest["sources"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

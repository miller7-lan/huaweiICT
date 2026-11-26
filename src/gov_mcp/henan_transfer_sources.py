"""Read the two pinned originals of the Henan household-transfer scenario; no network, no writes.

The originals are the same SHA-256-pinned bytes the frozen corpus is built from. The
workbook is read as literal cells (never evaluated); the norm is parsed with the same
HTML parser and article grouping as the frozen corpus. Nothing here approves a source
or establishes present policy.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from gov_mcp.henan_transfer import ARTICLES, CELL_IDS, FACTS
from gov_mcp.json_output import exact_json_equal, object_from_json
from gov_mcp.knowledge_fetch import parse_html, select_articles


SPECS = {
    "catalog": {
        "source_id": "HN-FEE-CATALOG-XLSX-2025",
        "url": "https://xcoss.henan.gov.cn/typtfile/20250318/fdace0fca875409fa1338179dd218530.xlsx",
        "original_sha256": "4aaddf9c385df36a3c53bd964b803a5c6d7ecca5d186cb8aee96a0e7d6e9562c",
        "kind": "xlsx", "region": "河南省",
    },
    "norm": {
        "source_id": "HN-NORM-PUBLISHER",
        "url": "https://hnga.henan.gov.cn/2022/08-16/2561597.html",
        "original_sha256": "aae6df5924be7648137bd582c873d57507fb379f3a3c37e1ff462631c05fbacb",
        "kind": "html", "region": "河南省",
    },
}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SHEET = "行政事业性收费"


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _require(condition, code):
    if not condition:
        raise ValueError("HENAN_TRANSFER_SOURCE_" + code)


def cells(raw):
    """The pinned workbook's literal shared strings for the fixed cells; formulas are refused."""
    with ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        _require(len({m.filename for m in members}) == len(members)
                 and all(m.file_size <= 5 * 1024 * 1024 for m in members)
                 and sum(m.file_size for m in members) <= 20 * 1024 * 1024, "WORKBOOK_MEMBERS")
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = book.find("s:sheets/s:sheet", NS)
        _require(sheet is not None and sheet.get("name") == SHEET, "WORKSHEET")
        strings = ["".join(node.itertext()) for node in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        values = {}
        for cell in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(".//s:c", NS):
            address = cell.get("r")
            if address not in CELL_IDS:
                continue
            value = cell.find("s:v", NS)
            _require(address not in values and cell.get("t") == "s" and cell.find("s:f", NS) is None
                     and value is not None, "LITERAL_CELL")
            index = int(value.text)
            _require(0 <= index < len(strings), "CELL_INDEX")
            values[address] = strings[index]
    _require(set(values) == set(CELL_IDS), "MISSING_CELL")
    return [(f"Excel工作表{SHEET}!" + c, values[c]) for c in CELL_IDS]


def articles(raw):
    chunks, _ = parse_html(raw, {"id": "content"}, min_text_length=1, excluded_selectors=[{"class": "operator"}])
    return [(row["locator"], row["text"]) for row in select_articles(chunks, ARTICLES)]


def _segment(locator, text, kind):
    _require(type(locator) is str and bool(locator) and type(text) is str and bool(text), "SEGMENT")
    return {"locator": locator, "text": text, "text_sha256": sha256(text.encode()).hexdigest(), "evidence_kind": kind}


class ArchivedHenanTransferSource:
    """originals_dir holds files named by their SHA-256; gates_path pauses the scenario."""

    def __init__(self, originals_dir, gates_path):
        self.originals_dir = Path(originals_dir)
        self.gates_path = Path(gates_path)

    @staticmethod
    def _gates(value):
        sources = value.get("sources")
        _require(value.get("business_conclusions_paused") is True and type(sources) is dict
                 and set(sources) == {spec["source_id"] for spec in SPECS.values()}, "GATE_IDS")
        for state in sources.values():
            _require(type(state) is dict and state.get("enabled") is True and "paused_reason" in state
                     and state["paused_reason"] is None and state.get("failed") is False
                     and state.get("superseded") is False, "SOURCE_PAUSED_OR_FAILED")

    def __call__(self):
        snapshots = {}
        gate_raw = self.gates_path.read_bytes()
        self._gates(object_from_json(gate_raw))
        documents = {}
        for name, spec in SPECS.items():
            path = self.originals_dir / spec["original_sha256"]
            _require(path.is_file() and path.stat().st_size <= 10 * 1024 * 1024, "ORIGINAL_FILE")
            raw = path.read_bytes()
            _require(sha256(raw).hexdigest() == spec["original_sha256"], "ORIGINAL_HASH")
            snapshots[name] = raw
            if name == "catalog":
                segments = [_segment(loc, text, "literal_cell") for loc, text in cells(raw)]
            else:
                segments = [_segment(loc, text, "exact_html_article") for loc, text in articles(raw)]
            documents[name] = {**spec, "revision": "archived-original-" + spec["original_sha256"][:24],
                               "matter": FACTS["matter"], "segments": segments,
                               "current_applicability": "unknown", "formal_approval": False,
                               "formally_published": False, "archive_mode": True}
        from gov_mcp.henan_transfer import build_candidate
        candidate = build_candidate(documents)
        for name, spec in SPECS.items():
            _require((self.originals_dir / spec["original_sha256"]).read_bytes() == snapshots[name],
                     "INPUT_CHANGED_DURING_LOAD")
        final_gates = self.gates_path.read_bytes()
        _require(final_gates == gate_raw, "GATE_CHANGED_DURING_LOAD")
        self._gates(object_from_json(final_gates))
        binding = digest({"inputs": {name: sha256(raw).hexdigest() for name, raw in snapshots.items()},
                          "gates": sha256(gate_raw).hexdigest(), "documents": digest(documents)})
        return {"candidate": candidate, "binding": binding,
                "freshness_basis": "fixed_archived_originals_not_live_recheck"}


def same_candidate(first, second):
    return exact_json_equal(first, second)

"""Read the three fixed Henan preparation originals without network or writes.

This adapter binds archived bytes and attributed AI observations. It neither
inherits earlier source approvals nor establishes present policy applicability.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from gov_mcp.json_output import exact_json_equal, object_from_json
from gov_mcp.knowledge_fetch import parse_html


SPECS = {
    "catalog": {
        "source_id": "HN-FEE-CATALOG-XLSX-2025",
        "url": "https://xcoss.henan.gov.cn/typtfile/20250318/fdace0fca875409fa1338179dd218530.xlsx",
        "original_sha256": "4aaddf9c385df36a3c53bd964b803a5c6d7ecca5d186cb8aee96a0e7d6e9562c",
        "kind": "xlsx", "region": "河南省",
    },
    "norm": {
        "source_id": "MOF-FEE-2007",
        "url": "https://www.mof.gov.cn/gkml/caizhengwengao/caizhengbuwengao2007/caizhengbuwengao20078/200805/t20080519_26881.htm",
        "original_sha256": "a77897e3863b5c61bbd0753d6d6feb48a3600a457624848a704f71145914ab66",
        "kind": "html", "region": "national",
    },
    "scan": {
        "source_id": "HN-MEASURES-2021-PDF",
        "url": "https://oss.henan.gov.cn/typtfile/20210601/c303a2a53f3f4396a25981f108532f9c.pdf",
        "original_sha256": "db257f4c918d95d6212c92262309ddd9a10ef33ec5354e0bbbf954df1ac52eff",
        "kind": "pdf", "region": "河南省",
    },
}
# These existing evidence files are immutable inputs, not new review decisions.
EVIDENCE_HASHES = {
    "preparation": "48f0af25772180176449712ab9540efd532fc0c402d9bfb084347c8888aed973",
    "extraction": "c1f7a18592b002133a3fd61a3ebac2f270c3cf18c8e4af210be1604ce57d12a5",
    "observation": "2dc6e6207a30ee94c0c132c290ace71071e6df43d4c6a54c8c34a9e557ddbea5",
}
CELLS = ("A1", "D4", "E4", "F4", "G4", "B17", "D36", "E36", "F36", "G36")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PAGE_HASHES = {
    5: "c235b11699fa32b9bdf9a7b9d19dc0a58fa8b0e95a1a51f0765fa1943234d80b",
    6: "b6adb0ea3e9d1cdd646e1071ede1288390768d28fd3dc7840ef12f7c5cc889d4",
}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _require(condition, code):
    if not condition:
        raise ValueError("HENAN_MULTIMODAL_SOURCE_" + code)


def _source_rows(report):
    rows = report.get("sources")
    _require(type(rows) is list and len(rows) == 3
             and all(type(row) is dict for row in rows), "SOURCE_ROWS")
    indexed = {row.get("source_id"): row for row in rows}
    _require(set(indexed) == {spec["source_id"] for spec in SPECS.values()}, "SOURCE_IDS")
    return indexed


def _cells(raw):
    """Read the pinned workbook's exact literal strings; never evaluate cells."""
    with ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        _require(len({member.filename for member in members}) == len(members)
                 and all(member.file_size <= 5 * 1024 * 1024 for member in members)
                 and sum(member.file_size for member in members) <= 20 * 1024 * 1024,
                 "WORKBOOK_MEMBERS")
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = book.find("s:sheets/s:sheet", NS)
        _require(sheet is not None and sheet.get("name") == "行政事业性收费", "WORKSHEET")
        relationship = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        links = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = [node.get("Target") for node in links if node.get("Id") == relationship]
        _require(len(targets) == 1 and targets[0] in
                 ("worksheets/sheet1.xml", "/xl/worksheets/sheet1.xml"), "WORKSHEET_RELATIONSHIP")
        strings = ["".join(node.itertext()) for node in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        values = {}
        for cell in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(".//s:c", NS):
            address = cell.get("r")
            if address not in CELLS:
                continue
            value = cell.find("s:v", NS)
            _require(address not in values and cell.get("t") == "s"
                     and cell.find("s:f", NS) is None and value is not None,
                     "LITERAL_CELL")
            index = int(value.text)
            _require(0 <= index < len(strings), "CELL_INDEX")
            values[address] = strings[index]
    _require(set(values) == set(CELLS), "MISSING_CELL")
    return [{"locator": "Excel工作表行政事业性收费!" + cell,
             "category": "fee", "text": values[cell]} for cell in CELLS]


def _segment(locator, text, kind):
    _require(type(locator) is str and bool(locator) and type(text) is str and bool(text), "SEGMENT")
    return {"locator": locator, "text": text, "text_sha256": sha256(text.encode()).hexdigest(),
            "evidence_kind": kind}


class ArchivedHenanMultimodalSource:
    def __init__(self, preparation_root, observation_path, extraction_path, gates_path):
        self.preparation_root = Path(preparation_root)
        self.observation_path = Path(observation_path)
        self.extraction_path = Path(extraction_path)
        self.gates_path = Path(gates_path)

    def _acquisition_state(self):
        """Respect the original collector's current state, through read-only SQL.

        Full configuration text is fingerprinted, never copied to the candidate
        or returned binding. Closing each read allows the final check to see a
        pause or configuration change committed during document extraction.
        """
        path = self.preparation_root / "acquisition" / "knowledge.sqlite3"
        _require(path.is_file(), "ACQUISITION_STATE_REQUIRED")
        identifiers = [spec["source_id"] for spec in SPECS.values()]
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                rows = connection.execute(
                    "SELECT id,config,next_check,last_success,last_request,failures,paused_reason "
                    "FROM sites WHERE id IN (?,?,?) ORDER BY id", identifiers).fetchall()
        except sqlite3.Error:
            raise ValueError("HENAN_MULTIMODAL_SOURCE_ACQUISITION_STATE_UNAVAILABLE") from None
        _require(len(rows) == 3 and {row["id"] for row in rows} == set(identifiers),
                 "ACQUISITION_SOURCE_IDS")
        by_id = {spec["source_id"]: spec for spec in SPECS.values()}
        state = {}
        for row in rows:
            _require(type(row["config"]) is str, "ACQUISITION_CONFIG")
            config = object_from_json(row["config"])
            source_id = row["id"]
            spec = by_id[source_id]
            parsed = urlsplit(spec["url"])
            access = config.get("access_review")
            _require(config.get("id") == source_id and config.get("enabled") is True
                     and type(row["failures"]) is int and row["failures"] == 0
                     and row["paused_reason"] in (None, "")
                     and type(access) is dict and access.get("approved") is True,
                     "ACQUISITION_SOURCE_PAUSED_OR_FAILED")
            _require(config.get("origin") == parsed.scheme + "://" + parsed.netloc
                     and type(config.get("seeds")) is list and spec["url"] in config["seeds"]
                     and type(config.get("allowed_paths")) is list and parsed.path in config["allowed_paths"],
                     "ACQUISITION_SOURCE_RETARGETED")
            state[source_id] = {
                "config_sha256": sha256(row["config"].encode()).hexdigest(),
                "enabled": True, "failures": row["failures"], "paused_reason": row["paused_reason"],
                "next_check": row["next_check"], "last_success": row["last_success"],
                "last_request": row["last_request"],
            }
        return state

    @staticmethod
    def _gates(value):
        sources = value.get("sources")
        _require(value.get("business_conclusions_paused") is True and type(sources) is dict
                 and set(sources) == {spec["source_id"] for spec in SPECS.values()}, "GATE_IDS")
        for state in sources.values():
            _require(type(state) is dict and state.get("enabled") is True
                     and "paused_reason" in state and state["paused_reason"] is None
                     and state.get("failed") is False and state.get("superseded") is False, "SOURCE_PAUSED_OR_FAILED")

    def __call__(self):
        # Every input is read into bytes once, then rechecked before returning.
        # The caller must also check its current gates before using an answer.
        snapshots = {}
        input_paths = {}

        def read(label, path, expected=None):
            path = Path(path)
            _require(path.is_file() and path.stat().st_size <= 10 * 1024 * 1024, "INPUT_FILE")
            raw = path.read_bytes()
            _require(expected is None or sha256(raw).hexdigest() == expected, "INPUT_HASH")
            snapshots[label] = raw
            input_paths[label] = path
            return raw

        gate_raw = read("gates", self.gates_path)
        self._gates(object_from_json(gate_raw))
        acquisition_state = self._acquisition_state()
        preparation = object_from_json(read("preparation", self.extraction_path.parent / "preparation.json",
                                            EVIDENCE_HASHES["preparation"]))
        extraction = object_from_json(read("extraction", self.extraction_path, EVIDENCE_HASHES["extraction"]))
        observation = object_from_json(read("observation", self.observation_path, EVIDENCE_HASHES["observation"]))
        prepared = _source_rows(preparation)
        extracted = _source_rows(extraction)
        _require(preparation.get("business_approval") is False
                 and preparation.get("ontology_activated") is False
                 and type(preparation.get("formal_publications")) is int
                 and preparation["formal_publications"] == 0, "PREPARATION_APPROVAL")
        _require(extraction.get("status") == "PASS_BOUND_SOURCE_PREPARATION"
                 and extraction.get("business_acceptance_complete") is False
                 and type(extraction.get("formal_publications")) is int
                 and extraction["formal_publications"] == 0, "EXTRACTION_APPROVAL")
        documents = {}
        for name, spec in SPECS.items():
            identifier = spec["source_id"]
            receipt = object_from_json(read(name + "_receipt", self.preparation_root / (identifier + "-attempt.json")))
            _require(exact_json_equal(receipt, prepared[identifier]), "RECEIPT_PREPARATION_BINDING")
            original = read(name + "_original", self.preparation_root / (identifier + "." + spec["kind"]),
                            spec["original_sha256"])
            _require(all(receipt.get(field) == spec[field] for field in
                         ("source_id", "url", "kind", "original_sha256"))
                     and receipt.get("state") == "acquired"
                     and type(receipt.get("http_status")) is int and receipt["http_status"] == 200
                     and receipt.get("final_url") == spec["url"]
                     and receipt.get("expected_original_sha256") == spec["original_sha256"]
                     and type(receipt.get("original_bytes")) is int and receipt["original_bytes"] == len(original)
                     and receipt.get("matches_historical_original") is True
                     and receipt.get("business_approval") is False, "ACQUISITION_RECEIPT")
            prior = extracted[identifier]
            _require(all(prior.get(field) == spec[field] for field in ("source_id", "url", "original_sha256"))
                     and prior.get("business_approval") is False
                     and prior.get("current_applicability") == "unknown", "EXTRACTION_SOURCE_BINDING")
            if name == "catalog":
                cells = _cells(original)
                _require(exact_json_equal(cells, prior.get("cells")), "CELL_EXTRACTION_BINDING")
                expected_cells = [{**cell, "text_sha256": sha256(cell["text"].encode()).hexdigest()} for cell in cells]
                _require(exact_json_equal(expected_cells, receipt.get("cells")), "CELL_RECEIPT_BINDING")
                segments = [_segment(cell["locator"], cell["text"], "literal_cell") for cell in cells]
            elif name == "norm":
                chunks, _ = parse_html(original, {"class": "my_doccontent"}, min_text_length=1)
                wanted = prior.get("clauses")
                _require(type(wanted) is list and len(wanted) == 5, "COMPLETE_HTML_CLAUSES")
                by_locator = {chunk["locator"]: chunk for chunk in chunks}
                selected = []
                for ordinal, row in enumerate(wanted, 3):
                    _require(type(row) is dict and row.get("locator") == "正文段落" + str(ordinal)
                             and row["locator"] in by_locator, "HTML_LOCATOR")
                    actual = by_locator[row["locator"]]
                    actual = {**actual, "text_sha256": sha256(actual["text"].encode()).hexdigest()}
                    _require(exact_json_equal(actual, row), "HTML_EXTRACTION_BINDING")
                    selected.append(actual)
                segments = [_segment(row["locator"], row["text"], "exact_html_clause") for row in selected]
            else:
                _require(observation.get("source_id") == identifier
                         and observation.get("original_sha256") == spec["original_sha256"]
                         and type(observation.get("reviewer")) is dict
                         and observation["reviewer"].get("kind") == "ai"
                         and observation.get("human_review_completed") is False
                         and observation.get("business_approval") is False
                         and observation.get("formally_published") is False
                         and observation.get("current_applicability") == "unknown"
                         and exact_json_equal(prior.get("observation"), observation), "VISUAL_PROVENANCE")
                pages = observation.get("rendered_pages")
                _require(type(pages) is list and len(pages) == 2
                         and [page.get("pdf_page") for page in pages] == [5, 6], "VISUAL_PAGES")
                for page in pages:
                    number = page["pdf_page"]
                    _require(type(number) is int and page.get("image_sha256") == PAGE_HASHES[number], "VISUAL_PAGE_HASH")
                    read("scan_page_" + str(number), self.preparation_root / ("scan-" + str(number) + ".png"),
                         PAGE_HASHES[number])
                observations = observation.get("observations")
                _require(type(observations) is list and len(observations) == 2, "VISUAL_OBSERVATIONS")
                segments = [_segment(row["locator"], row["heading"] + "\n" + row["transcription"],
                                     "ai_visual_transcription") for row in observations]
            doc = {**spec, "revision": "archived-original-" + spec["original_sha256"][:24],
                   "matter": "resident-id-renewal", "segments": segments,
                   "current_applicability": "unknown", "formal_approval": False,
                   "formally_published": False, "archive_mode": True}
            if name == "scan":
                doc["visual_provenance"] = {key: observation[key] for key in
                    ("reviewer", "rendered_pages", "method", "human_review_completed")}
            documents[name] = doc

        from gov_mcp.henan_multimodal import build_candidate
        candidate = build_candidate(documents)
        _require(all(path.read_bytes() == snapshots[label] for label, path in input_paths.items()),
                 "INPUT_CHANGED_DURING_LOAD")
        _require(exact_json_equal(self._acquisition_state(), acquisition_state),
                 "ACQUISITION_STATE_CHANGED_DURING_LOAD")
        final_gates = self.gates_path.read_bytes()
        _require(final_gates == gate_raw, "GATE_CHANGED_DURING_LOAD")
        self._gates(object_from_json(final_gates))
        binding = digest({"inputs": {key: sha256(raw).hexdigest() for key, raw in snapshots.items()},
                          "acquisition_sites": acquisition_state,
                          "documents": digest(documents)})
        return {"candidate": candidate, "binding": binding,
                "freshness_basis": "fixed_archived_originals_not_live_recheck"}

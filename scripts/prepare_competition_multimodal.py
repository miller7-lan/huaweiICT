"""One bounded acquisition of three public originals; no policy approval/model.

Default writes a plan only. --acquire keeps receipts and original bytes in a
new private scope. Existing attempts, including failures, are never retried.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import AccessStopped, BOT, Fetcher, MAX_BYTES
from gov_mcp.knowledge_pdf import extract_document
from private_runtime import ensure_private_directory
from record_fee_norm_evidence import workbook_cells

RUNTIME = ROOT / ".runtime/multimodal-preparation-20261021"
OUTPUT = ROOT / "evidence/competition-multimodal-20261021"
SPECS = {
    "HN-FEE-CATALOG-XLSX-2025": ("https://xcoss.henan.gov.cn/typtfile/20250318/fdace0fca875409fa1338179dd218530.xlsx",
                                  "4aaddf9c385df36a3c53bd964b803a5c6d7ecca5d186cb8aee96a0e7d6e9562c", "xlsx"),
    "HN-MEASURES-2021-PDF": ("https://oss.henan.gov.cn/typtfile/20210601/c303a2a53f3f4396a25981f108532f9c.pdf",
                             "db257f4c918d95d6212c92262309ddd9a10ef33ec5354e0bbbf954df1ac52eff", "pdf"),
    "MOF-FEE-2007": ("https://www.mof.gov.cn/gkml/caizhengwengao/caizhengbuwengao2007/caizhengbuwengao20078/200805/t20080519_26881.htm",
                     "a77897e3863b5c61bbd0753d6d6feb48a3600a457624848a704f71145914ab66", "html"),
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PreparationFetcher(Fetcher):
    def _request(self, url, headers):
        allowed = {s[0] for s in SPECS.values()} | {
            urlsplit(s[0]).scheme + "://" + urlsplit(s[0]).netloc + "/robots.txt" for s in SPECS.values()}
        if url not in allowed:
            raise AccessStopped("Outside fixed acquisition paths")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM audit WHERE action='multimodal_http_reserved'").fetchone()[0] >= 12:
                raise AccessStopped("Fixed acquisition request budget exhausted")
            self.store.log(db, "multimodal_http_reserved", {"url": url})
        proxies = urllib.request.getproxies()
        proxy = proxies.get("https") or proxies.get("http")
        if proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}), NoRedirect(),
                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            req = urllib.request.Request(url, headers={"User-Agent": BOT + "/0.1 (public-document research)",
                                                      "Accept-Encoding": "identity", **headers})
            try:
                response = opener.open(req, timeout=20)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_BYTES + 1)
                status = response.status
                result_headers = {k.lower(): v for k, v in response.headers.items()}
                if len(raw) > MAX_BYTES:
                    raise AccessStopped("Response too large")
        else:
            status, result_headers, raw = super()._request(url, headers)
        with self.store.db() as db:
            self.store.log(db, "multimodal_http_result", {"url": url, "http_status": status,
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "uses_configured_proxy": bool(proxy)})
        return status, result_headers, raw


def acquire():
    ensure_private_directory(RUNTIME)
    store = KnowledgeStore.initialize(RUNTIME / "acquisition")
    results = []
    for identifier, (url, expected, kind) in SPECS.items():
        attempt = RUNTIME / (identifier + "-attempt.json")
        if attempt.exists():
            results.append(json.loads(attempt.read_text("utf-8")))
            continue
        parsed = urlsplit(url)
        origin = parsed.scheme + "://" + parsed.netloc
        basis = "Private engineering source preparation, attributed evidence only; no redistribution or current-policy approval."
        config = {"id": identifier, "label": identifier, "enabled": True, "origin": origin,
                  "allowed_paths": [parsed.path], "seeds": [url], "interval_seconds": 86400,
                  "delay_seconds": 5, "discover_links": False,
                  "metadata": {"title": identifier, "region": "河南省" if kind != "html" else "national",
                      "matter": "resident-id-renewal", "usage_basis": basis,
                      "collection_role": "engineering_candidate", "business_review": "pending"},
                  "access_review": {"approved": True, "reviewer": "codex_automated_technical_access_screen",
                      "actor_kind": "machine", "checked_at": datetime.now(timezone.utc).isoformat(),
                      "usage_basis": basis, "business_review_completed": False}}
        store.register(config)
        record = {"source_id": identifier, "url": url, "expected_original_sha256": expected,
                  "kind": kind, "attempted_at": datetime.now(timezone.utc).isoformat(),
                  "state": "reserved", "business_approval": False}
        write(attempt, record)  # Crash or access failure cannot silently re-open the attempt.
        try:
            status, headers, raw, final_url = PreparationFetcher(store).fetch(config, url)
            if status != 200 or final_url != url:
                raise AccessStopped("Exact successful original response required", http_status=status)
            actual = hashlib.sha256(raw).hexdigest()
            path = RUNTIME / (identifier + "." + kind)
            path.write_bytes(raw)
            record.update(state="acquired", http_status=status, final_url=final_url,
                          original_sha256=actual, original_bytes=len(raw), matches_historical_original=actual == expected)
            if actual != expected:
                record.update(extraction_state="blocked_changed_original_requires_new_review")
            elif kind == "xlsx":
                cells = workbook_cells(path)
                record.update(extraction_state="exact_literal_cells_read", cells=[{**c,
                    "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()} for c in cells])
            elif kind == "pdf":
                result = extract_document(path, {"engine": "windows.media.ocr", "language": "zh-Hans-CN"})
                write(RUNTIME / "scan-extraction.json", result)
                record.update(extraction_state="ocr_pending_visual_review", pages=len(result["chunks"]),
                    extraction_method=result["extraction"]["method"], requires_visual_review=True,
                    selected_pages=[{**chunk, "text_sha256": hashlib.sha256(chunk["text"].encode()).hexdigest()}
                                    for chunk in result["chunks"] if chunk["locator"].startswith(("PDF第5页", "PDF第6页"))])
            else:
                # Original is retained in full. Locate complete relevant clauses
                # using the archived catalog; a changed location is not repaired.
                from gov_mcp.knowledge_fetch import parse_html
                catalog = json.loads((ROOT / "data/knowledge/candidate-catalog.json").read_text("utf-8"))
                doc = next(d for d in catalog["documents"] if d["source_id"] == identifier)
                chunks, _ = parse_html(raw, {"class": "my_doccontent"}, min_text_length=1)
                wanted = {c["text_sha256"] for c in doc["selected_clauses"]}
                matched = [c for c in chunks if hashlib.sha256(c["text"].encode()).hexdigest() in wanted]
                record.update(extraction_state="matched_catalog_clauses" if len(matched) == len(wanted) else "selector_or_clause_review_required",
                              matched_clauses=matched, expected_clause_count=len(wanted))
        except Exception as error:
            detail = error.detail() if isinstance(error, AccessStopped) else {}
            record.update(state="stopped" if record["state"] == "reserved" else record["state"],
                          processing_stopped=True, error_type=type(error).__name__, access_diagnostic=detail)
        write(attempt, record)
        results.append(record)
    with store.db() as db:
        requests = db.execute("SELECT COUNT(*) FROM audit WHERE action='multimodal_http_reserved'").fetchone()[0]
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "sources": results,
              "http_reservations": requests, "model_calls": 0, "business_approval": False,
              "ontology_activated": False, "formal_publications": 0, "source_originals_redistributed": False,
              "scope": "Actual bounded acquisition and extraction only; not a multimodal business acceptance."}
    write(OUTPUT / "preparation.json", report)
    return report


def extract_local():
    """Recheck already acquired bytes; never repeat HTTP or bypass OS policy."""
    from gov_mcp.knowledge_fetch import parse_html
    records = []
    catalog = json.loads((ROOT / "data/knowledge/candidate-catalog.json").read_text("utf-8"))
    for identifier, (url, expected, kind) in SPECS.items():
        path = RUNTIME / (identifier + "." + kind)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("EXACT_ACQUIRED_ORIGINAL_REQUIRED")
        record = {"source_id": identifier, "url": url, "original_sha256": expected,
                  "business_approval": False, "current_applicability": "unknown"}
        if kind == "xlsx":
            record.update(extraction_method="exact_literal_xlsx_cells", cells=workbook_cells(path))
        elif kind == "html":
            doc = next(d for d in catalog["documents"] if d["source_id"] == identifier)
            wanted = {c["text_sha256"] for c in doc["selected_clauses"]}
            chunks, _ = parse_html(path.read_bytes(), {"class": "my_doccontent"}, min_text_length=1)
            matched = [{**c, "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()}
                       for c in chunks if hashlib.sha256(c["text"].encode()).hexdigest() in wanted]
            if len(matched) != len(wanted):
                raise ValueError("COMPLETE_CATALOG_CLAUSES_REQUIRED")
            record.update(extraction_method="exact_catalog_clause_hash_match", clauses=matched)
        else:
            visual = json.loads((OUTPUT / "scan-visual-observation.json").read_text("utf-8"))
            if visual["original_sha256"] != expected or visual["reviewer"]["kind"] != "ai":
                raise ValueError("BOUND_ATTRIBUTED_VISUAL_OBSERVATION_REQUIRED")
            for page in visual["rendered_pages"]:
                image_path = RUNTIME / ("scan-" + str(page["pdf_page"]) + ".png")
                if hashlib.sha256(image_path.read_bytes()).hexdigest() != page["image_sha256"]:
                    raise ValueError("OBSERVED_RENDER_CHANGED")
            record.update(extraction_method="ai_visual_transcription_of_rendered_pdf",
                          automated_ocr_state="blocked_by_windows_execution_policy",
                          execution_policy_changed=False, observation=visual,
                          requires_business_review=True)
        records.append(record)
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "status": "PASS_BOUND_SOURCE_PREPARATION",
              "source_count": len(records), "sources": records, "new_http_calls": 0, "model_calls": 0,
              "formal_publications": 0, "business_acceptance_complete": False,
              "scope": "Exact cells, full normative clauses and attributed scan transcription; no joint decision or policy approval."}
    write(OUTPUT / "local-extraction.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--acquire", action="store_true")
    mode.add_argument("--extract-local", action="store_true")
    args = parser.parse_args()
    result = acquire() if args.acquire else extract_local() if args.extract_local else {
        "status": "PLAN_ONLY", "sources": list(SPECS), "http_calls": 0, "model_calls": 0}
    print(json.dumps({k: v for k, v in result.items() if k != "sources"} | {"sources": [
        {k: r.get(k) for k in ("source_id", "state", "matches_historical_original", "extraction_state")}
        if isinstance(r, dict) else {"source_id": r} for r in result.get("sources", [])]}, ensure_ascii=True))

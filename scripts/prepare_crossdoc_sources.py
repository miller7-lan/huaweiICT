"""One bounded official acquisition, then offline archive binding and reasoning."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.crossdoc_reasoning import (ARCHIVE_MODE, SCENARIO_FACTS, SCOPE_MAPPING,
                                      SOURCE_IDENTITY, checked_segments, load_bundle, solve)
from gov_mcp.knowledge_fetch import BOT, MAX_BYTES
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/crossdoc-native-preview-20261019"
OUTPUT = ROOT / "evidence/crossdoc-native-preview-20261019"
ROBOTS_URL = "https://www.nanjing.gov.cn/robots.txt"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def acquire(root=RUNTIME, output=OUTPUT):
    root, output = Path(root), Path(output)
    ensure_private_directory(root)
    marker = root / "acquisition-attempt.json"
    # A failed network attempt must not accidentally receive a fresh budget.
    if marker.exists():
        raise ValueError("ACQUISITION_ALREADY_ATTEMPTED_USE_OFFLINE_PREPARE")
    receipt = {"format": "crossdoc-acquisition-v1", "started_at": datetime.now(timezone.utc).isoformat(),
               "status": "started", "max_http_attempts": 2, "requests": [], "cloud_model_calls": 0,
               "method": "existing_configured_proxy_fixed_https_tls_no_redirect_robots",
               "formally_published": False, "formal_approval": False}
    # Exclusive marker persists before credentials/proxy discovery or network.
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream)
    try:
        proxies = urllib.request.getproxies()
        proxy = proxies.get("https") or proxies.get("http")
        if not proxy:
            raise ValueError("EXISTING_CONFIGURED_PROXY_REQUIRED")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}), NoRedirect(),
                                            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        sources = root / "sources"
        sources.mkdir(exist_ok=True)
        last_started = None
        def request(url, delay=5):
            nonlocal last_started
            if url not in (ROBOTS_URL, SOURCE_IDENTITY["old_card"]["url"]) or len(receipt["requests"]) >= 2:
                raise ValueError("FIXED_URL_OR_HTTP_BUDGET_EXCEEDED")
            if last_started is not None:
                time.sleep(max(0, delay - (time.monotonic() - last_started)))
            last_started = time.monotonic()
            item = {"url": url, "started_at": datetime.now(timezone.utc).isoformat(), "http_status": None}
            receipt["requests"].append(item)
            write(marker, receipt)
            req = urllib.request.Request(url, headers={"User-Agent": BOT + "/0.1 (public-document research)",
                                                       "Accept-Encoding": "identity"})
            try:
                response = opener.open(req, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                item["http_status"] = response.status
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("HTTP_RESPONSE_TOO_LARGE")
                item.update(bytes=len(raw), sha256=sha(raw))
                # Preserve actual returned public original even on later validation failure.
                target = sources / ("robots.txt" if url == ROBOTS_URL else SOURCE_IDENTITY["old_card"]["source_id"] + ".html")
                target.write_bytes(raw)
                write(marker, receipt)
                return response.status, raw
        status, robots = request(ROBOTS_URL)
        if (status not in (200, 404, 410)
                or status == 200 and re.search(br"<!doctype\s+html|<html\b", robots, re.I)):
            raise ValueError("ROBOTS_POLICY_UNAVAILABLE")
        policy = RobotFileParser(ROBOTS_URL)
        policy.parse(robots.decode("utf-8").splitlines() if status == 200 else [])
        if status == 200 and not policy.can_fetch(BOT, SOURCE_IDENTITY["old_card"]["url"]):
            raise ValueError("ROBOTS_DENIED")
        rate = policy.request_rate(BOT) if status == 200 else None
        delay = max(5, (policy.crawl_delay(BOT) or 0) if status == 200 else 0,
                    rate.seconds / rate.requests if rate else 0)
        if delay > 60:
            raise ValueError("ROBOTS_DELAY_REQUIRES_SEPARATE_RUN")
        status, raw = request(SOURCE_IDENTITY["old_card"]["url"], delay)
        if status != 200 or sha(raw) != SOURCE_IDENTITY["old_card"]["raw_sha256"]:
            raise ValueError("ORIGINAL_HTTP_OR_CATALOG_HASH_MISMATCH")
        receipt.update(status="acquired_original_pending_offline_validation", completed_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        receipt.update(status="stopped", error_type=type(error).__name__,
                       # Exception text/remote bodies may contain transport secrets.
                       reason=str(error) if type(error) is ValueError else "EXTERNAL_ACQUISITION_FAILED_NO_RETRY")
    write(marker, receipt)
    write(output / "source-acquisition.json", receipt)
    return receipt


def prepare(root=RUNTIME, output=OUTPUT):
    root, output = Path(root), Path(output)
    ensure_private_directory(root)
    marker = root / "manifest.json"
    if marker.exists():
        return load_bundle(root)
    catalog_raw = (ROOT / "data/knowledge/candidate-catalog.json").read_bytes()
    catalog = json.loads(catalog_raw)
    specs = [item for path in sorted((ROOT / "data/knowledge/expansion-batches").glob("*.json"))
             for item in json.loads(path.read_bytes())["sources"]]
    manifest = {"format": "crossdoc-source-bundle-v1", "archive_mode": ARCHIVE_MODE,
                "created_at": datetime.now(timezone.utc).isoformat(), "current_applicability": "unknown",
                "formal_approval": False, "formally_published": False,
                "catalog_sha256": sha(catalog_raw), "sources": {},
                "freshness_note": "Frozen historical archives; original acquisition dates retained, no TTL renewal or current applicability inference."}
    (root / "sources").mkdir(exist_ok=True)
    acquisition = json.loads((root / "acquisition-attempt.json").read_bytes())
    if acquisition["status"] != "acquired_original_pending_offline_validation":
        raise ValueError("SUCCESSFUL_SINGLE_ACQUISITION_REQUIRED")
    for key, identity in SOURCE_IDENTITY.items():
        matches = [d for d in catalog["documents"] if d["source_id"] == identity["source_id"] and d["url"] == identity["url"]]
        found_specs = [s for s in specs if s["source_id"] == identity["source_id"] and s["url"] == identity["url"]]
        if len(matches) != 1 or len(found_specs) != 1:
            raise ValueError("UNIQUE_CATALOG_SOURCE_AND_SPEC_REQUIRED")
        document, spec = matches[0], found_specs[0]
        relative = "sources/" + identity["source_id"] + ".html"
        if key == "cycle":
            previous_root = ROOT / ".runtime/new-knowledge-experiment-20261018"
            raw = (previous_root / (identity["source_id"] + ".html")).read_bytes()
            snapshot = json.loads((previous_root / "snapshot.json").read_bytes())
            original_receipt = next(r for r in snapshot["acquisitions"] if r["source_id"] == identity["source_id"])
            if original_receipt.get("original_sha256") != identity["raw_sha256"] or original_receipt.get("http_status") != 200:
                raise ValueError("ORIGINAL_CYCLE_ACQUISITION_RECEIPT_REQUIRED")
            (root / relative).write_bytes(raw)
            provenance = {"method": "reused_existing_official_archive_no_http", "original_retrieved_at": original_receipt["checked_at"],
                          "original_receipt": original_receipt}
        else:
            raw = (root / relative).read_bytes()
            provenance = {"method": acquisition["method"], "original_retrieved_at": acquisition["completed_at"],
                          "original_receipt": acquisition}
        segments = checked_segments(raw, spec, document)
        manifest["sources"][key] = {**identity, "raw_path": relative, "spec": spec, "catalog_document": document,
            "segments": segments, "published_at": document["metadata"].get("published_at"),
            "effective_from": None, "effective_to": None, "current_applicability": "unknown",
            "formal_approval": False, "formally_published": False, "provenance": provenance}
    gates = {"format": "crossdoc-source-gates-v1", "archive_mode": ARCHIVE_MODE,
             "business_conclusions_paused": True, "scope_mapping": deepcopy(SCOPE_MAPPING),
             "sources": {key: {"enabled": True, "paused_reason": None, "failed": False, "superseded": False}
                         for key in SOURCE_IDENTITY}}
    write(root / "gates.json", gates)
    write(marker, manifest)
    bundle = load_bundle(root)
    write(output / "source-bundle.json", manifest)
    write(output / "source-gates.json", gates)
    write(output / "offline-source-reasoning.json", {"binding": bundle["binding"], "result": solve(bundle["documents"], SCENARIO_FACTS)})
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--acquire", action="store_true", help="At most robots + one fixed official old-card original")
    args = parser.parse_args()
    if args.acquire:
        receipt = acquire()
        if receipt["status"] == "stopped":
            print(json.dumps({"status": "stopped", "requests": len(receipt["requests"]), "reason": receipt["reason"]}))
            raise SystemExit(1)
    bundle = prepare()
    answer = solve(bundle["documents"], SCENARIO_FACTS)
    result_path = OUTPUT / "offline-source-reasoning.json"
    if result_path.exists():
        previous = json.loads(result_path.read_bytes())
        if previous["result"]["status"] != "derived":
            write(OUTPUT / "offline-source-reasoning-first-parser-check.json", previous)
    write(result_path, {"binding": bundle["binding"], "result": answer})
    print(json.dumps({"binding": bundle["binding"], "status": answer["status"], "conclusion": answer["conclusion"]}, ensure_ascii=False))

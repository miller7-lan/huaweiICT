"""Acquire two fixed official sources once, then replay isolated candidate retrieval offline."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import Fetcher, AccessStopped, BOT, MAX_BYTES
from gov_mcp.knowledge_landing import TARGETS, URLS, verified_chunks, retrieve
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/new-knowledge-experiment-20261018"
OUTPUT = ROOT / "evidence/new-knowledge-experiment"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configured_proxy_request(url, headers):
    # Only fixed public HTTPS URLs are admitted. DNS resolution occurs at the
    # user's explicit proxy; never send arbitrary hosts or private input here.
    allowed = set(URLS.values()) | {f"https://{urlsplit(u).netloc}/robots.txt" for u in URLS.values()}
    if url not in allowed:
        raise AccessStopped("Outside fixed proxy experiment allowlist")
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https") or proxies.get("http")
    if not proxy:
        raise AccessStopped("Configured proxy required")
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
        if len(raw) > MAX_BYTES:
            raise AccessStopped("Response too large")
        return response.status, {k.lower(): v for k, v in response.headers.items()}, raw


def acquire(repair_proxy=False):
    # An attempt marker prevents an offline replay or accidental repeated command
    # from re-requesting blocked origins or destroying a prior observation.
    if repair_proxy:
        previous = json.loads((RUNTIME / "snapshot.json").read_text(encoding="utf-8"))
        if (RUNTIME / "proxy-repair.json").exists() or previous["bindings"] or not all(
                not a["requests"] and a.get("access_failure") == {"phase": "robots", "http_status": None}
                for a in previous["acquisitions"]):
            raise ValueError("Proxy repair only permitted after a local pre-request stop")
        write(RUNTIME / "direct-attempt.json", previous)
        write(RUNTIME / "proxy-repair.json", {"reason": "Local fake-IP DNS blocks direct fetch; fixed public destinations via existing user proxy"})
    elif (RUNTIME / "attempt.json").exists():
        raise ValueError("Acquisition already attempted; use offline replay")
    store = KnowledgeStore.initialize(RUNTIME)
    ensure_private_directory(RUNTIME)
    stamp = datetime.now(timezone.utc).isoformat()
    proxies = urllib.request.getproxies()
    use_proxy = repair_proxy or bool(proxies.get("https") or proxies.get("http"))
    write(RUNTIME / "attempt.json", {"started_at": stamp, "uses_configured_proxy": use_proxy})
    catalog_raw = (ROOT / "data/knowledge/candidate-catalog.json").read_bytes()
    catalog = json.loads(catalog_raw)
    documents = {(d["source_id"], d["url"]): d for d in catalog["documents"]}
    specs = [s for p in sorted((ROOT / "data/knowledge/expansion-batches").glob("*.json"))
             for s in json.loads(p.read_text(encoding="utf-8"))["sources"] if s["source_id"] in TARGETS]
    if len(specs) != 2 or {s["source_id"] for s in specs} != set(TARGETS):
        raise ValueError("Two fixed sources required")
    snapshot = {"captured_at": stamp, "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
                "bindings": [], "acquisitions": [], "cloud_calls": 0, "formally_published": False}
    for spec in specs:
        if spec["url"] != URLS[spec["source_id"]]:
            raise ValueError("Fixed official URL required")
        document = documents[(spec["source_id"], spec["url"])]
        target = urlsplit(spec["url"])
        basis = "Official public service text; private attributed engineering preview; no business approval or republication of website assets"
        metadata = dict(document["metadata"], effective_from=None, effective_to=None,
                        usage_basis=basis, scope_confirmed=False, business_review="pending")
        config = {"id": spec["source_id"], "label": metadata["title"], "enabled": True,
                  "origin": f"{target.scheme}://{target.netloc}", "allowed_paths": [target.path],
                  "seeds": [spec["url"]], "interval_seconds": 86400, "delay_seconds": 5,
                  "content_selector": spec["selector"], "metadata": metadata,
                  "access_review": {"approved": True, "reviewer": "codex_automated_technical_access_screen",
                      "actor_kind": "machine", "checked_at": stamp, "usage_basis": basis,
                      "business_review_completed": False, "purpose": "private_engineering_candidates_only"}}
        store.register(config)
        receipt = {"source_id": spec["source_id"], "url": spec["url"], "checked_at": datetime.now(timezone.utc).isoformat(),
                   "method": "normal_configured_proxy_fixed_https_tls_robots" if use_proxy else "normal_direct_public_http_checked_ip_tls_robots", "requests": []}
        fetcher = Fetcher(store)
        request = configured_proxy_request if use_proxy else fetcher._request

        def observe(url, headers):
            status, response_headers, raw = request(url, headers)
            receipt["requests"].append({"url": url, "http_status": status,
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            if urlsplit(url).path == "/robots.txt":
                (RUNTIME / (spec["source_id"] + "-robots.txt")).write_bytes(raw)
            return status, response_headers, raw

        fetcher._request = observe
        try:
            status, _, raw, final_url = fetcher.fetch(config, spec["url"])
            receipt["http_status"] = status
            if status != 200 or final_url != spec["url"]:
                raise ValueError("Expected exact official document URL and HTTP 200")
            # Preserve observed original even if its selected text has drifted.
            (RUNTIME / (spec["source_id"] + ".html")).write_bytes(raw)
            selected = verified_chunks(raw, spec, document)
            revision = store.ingest(spec["source_id"], spec["url"], raw, metadata, selected)
            receipt.update(status="ingested_pending_review", original_sha256=hashlib.sha256(raw).hexdigest(),
                catalog_original_sha256=spec["original_sha256"],
                raw_matches_catalog=hashlib.sha256(raw).hexdigest() == spec["original_sha256"],
                all_selected_quotes_match=True, selected_chunks=len(selected), revision_id=revision)
            snapshot["bindings"].append({"spec": spec, "document": document, "revision_id": revision})
        except (OSError, ValueError) as error:
            receipt.update(status="stopped", error_type=type(error).__name__)
            if isinstance(error, AccessStopped):
                receipt["access_failure"] = error.detail()
            # Never store remote error bodies or automatically retry a block.
        snapshot["acquisitions"].append(receipt)
        write(RUNTIME / "snapshot.json", snapshot)
    return snapshot


CASES = [
    ("beijing_materials", "北京市", "resident-id-renewal", "身份证期满换领需要哪些材料？"),
    ("nanjing_cycle", "南京市", "resident-id-renewal", "身份证到期换证，省内和省外户籍分别多久？考生怎么办？"),
    ("erqi_isolation", "郑州市二七区", "resident-id-renewal", "身份证换领材料和时限"),
    ("passport_isolation", "北京市", "ordinary-passport-first-application", "首次申领护照材料"),
    ("online_branch", "北京市", "resident-id-renewal", "到期换领可以直接网上办理吗？损坏换领有什么条件？"),
]


def replay(snapshot):
    if snapshot["catalog_sha256"] != hashlib.sha256((ROOT / "data/knowledge/candidate-catalog.json").read_bytes()).hexdigest():
        raise ValueError("Catalog changed; old experiment must not be silently reused")
    store = KnowledgeStore(RUNTIME, read_only=True)
    results = [{"case_id": key, "question": query, "result": retrieve(store, snapshot["bindings"], query, region, matter)}
               for key, region, matter, query in CASES]
    checks = {
        "two_sources_acquired": len(snapshot["bindings"]) == 2,
        "beijing_has_real_quotes": bool(results[0]["result"]["citations"]),
        "nanjing_has_real_quotes": bool(results[1]["result"]["citations"]),
        "region_isolation": all(c["source_id"] == "BJ-ID-RENEWAL-2025" for c in results[0]["result"]["citations"])
            and all(c["source_id"] == "NJ-ID-RENEWAL-CYCLE-2026" for c in results[1]["result"]["citations"]),
        "erqi_blocked": results[2]["result"]["status"] == "OUT_OF_SCOPE" and not results[2]["result"]["citations"],
        "other_matter_blocked": results[3]["result"]["status"] == "OUT_OF_SCOPE" and not results[3]["result"]["citations"],
        "business_conclusions_paused": all(r["result"]["business_conclusion_status"] == "PAUSED_UNREVIEWED" for r in results),
        "online_conditions_preserved": any(
            {"正文段落24", "正文段落25"} <= {c["locator"] for c in context["selected_clauses"]}
            for context in results[4]["result"]["source_context"]),
    }
    with store.db() as db:
        counts = {t: db.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                  for t in ("sites", "documents", "revisions", "chunks", "reviews", "versions", "heads")}
    checks["no_review_or_publication"] = counts["reviews"] == counts["versions"] == counts["heads"] == 0
    report = {"status": "PASS_SOURCE_LANDING" if all(checks.values()) else "INCOMPLETE",
              "checks": checks, "counts": counts, "snapshot": snapshot, "queries": results,
              "limits": "Real acquisition and deterministic candidate retrieval only; no new Nexent run, cloud model, formal business approval or publication"}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / "result.json", report)
    print(json.dumps({"status": report["status"], "counts": counts, "checks": checks}, ensure_ascii=False))
    return all(checks.values())


def ingest_saved():
    """Repair parser settings using saved bytes, without another HTTP request."""
    snapshot = json.loads((RUNTIME / "snapshot.json").read_text(encoding="utf-8"))
    if (RUNTIME / "saved-ingest.json").exists():
        raise ValueError("Saved ingestion already completed; use replay")
    write(RUNTIME / "before-saved-ingest.json", snapshot)
    if (OUTPUT / "result.json").exists():
        write(OUTPUT / "before-parser-fix.json", json.loads((OUTPUT / "result.json").read_text(encoding="utf-8")))
    catalog = json.loads((ROOT / "data/knowledge/candidate-catalog.json").read_text(encoding="utf-8"))
    specs = [s for p in sorted((ROOT / "data/knowledge/expansion-batches").glob("*.json"))
             for s in json.loads(p.read_text(encoding="utf-8"))["sources"] if s["source_id"] in TARGETS]
    store = KnowledgeStore(RUNTIME)
    for receipt in snapshot["acquisitions"]:
        if receipt["status"] != "stopped" or receipt.get("error_type") != "ValueError" or receipt.get("http_status") != 200:
            continue
        spec = next(s for s in specs if s["source_id"] == receipt["source_id"])
        document = next(d for d in catalog["documents"] if d["source_id"] == spec["source_id"] and d["url"] == spec["url"])
        raw = (RUNTIME / (spec["source_id"] + ".html")).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec["original_sha256"] or not any(r["url"] == spec["url"] and r["sha256"] == digest for r in receipt["requests"]):
            raise ValueError("Saved original does not match captured and shared fingerprints")
        selected = verified_chunks(raw, spec, document)
        with store.db() as db:
            metadata = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (spec["source_id"],)).fetchone()[0])["metadata"]
        revision = store.ingest(spec["source_id"], spec["url"], raw, metadata, selected)
        snapshot["bindings"].append({"spec": spec, "document": document, "revision_id": revision})
        receipt.update(status="ingested_pending_review", previous_stop="ValueError: paragraph layout; short headings now retained",
                       original_sha256=digest, catalog_original_sha256=spec["original_sha256"], raw_matches_catalog=True,
                       all_selected_quotes_match=True, selected_chunks=len(selected), revision_id=revision,
                       offline_parser_repair=True)
    write(RUNTIME / "saved-ingest.json", {"parser_min_text_length": 1, "network_requests": 0})
    write(RUNTIME / "snapshot.json", snapshot)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquire", action="store_true", help="One bounded live public acquisition; no model calls")
    parser.add_argument("--repair-proxy", action="store_true", help="Once only after local fake-IP pre-request stop; no remote denial retry")
    parser.add_argument("--ingest-saved", action="store_true", help="Offline parser repair from exact saved originals; no HTTP")
    args = parser.parse_args()
    if sum((args.acquire, args.repair_proxy, args.ingest_saved)) > 1:
        parser.error("Choose one operation")
    snapshot = (ingest_saved() if args.ingest_saved else acquire(args.repair_proxy) if args.acquire or args.repair_proxy
                else json.loads((RUNTIME / "snapshot.json").read_text(encoding="utf-8")))
    raise SystemExit(0 if replay(snapshot) else 1)


if __name__ == "__main__":
    main()

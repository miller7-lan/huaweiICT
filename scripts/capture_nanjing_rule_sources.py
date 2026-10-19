"""Capture a fixed public source set once; never activates an old runtime or model."""
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

RUNTIME = ROOT / ".runtime/real-matter-closure/nanjing-source-v1"
SOURCES = (
    ("NJ-POLICE-FAQ-2025", "https://gaj.nanjing.gov.cn/njsgaj/202505/t20250529_5573116.html", "南京公安换证问答", "html", "2025-05-29"),
    ("NJ-CYCLE-2026", "https://www.nanjing.gov.cn/hdjl/hygq/202601/t20260105_5757179.html", "南京市政府换证周期答复", "html", "2026-01-05"),
    ("NJ-OLD-CARD-2025", "https://www.nanjing.gov.cn/zgnjsjb/hdjl/rdwt/202512/t20251209_5709611.html", "南京市政府旧证使用答复", "html", "2025-12-09"),
    ("NPC-ID-LAW-2011", "https://wb.flk.npc.gov.cn/flfg/PDF/481dce0469334593bc9050435f09d60e.pdf", "全国人大常委会公报居民身份证法", "pdf", None),
)
LAW_MIRROR = (
    ("LAW-ID-BJ-POLICE-2011", "https://gaj.beijing.gov.cn/zhengce/zcfg/202204/t20220402_2653202.html", "北京公安公布的2011年修正居民身份证法", "html", None),
)
NATIONAL_LAW = (
    ("LAW-ID-BJ-TRAFFIC-2011", "https://jtgl.beijing.gov.cn/jgj/jgxx/flfg/fl/203777/index.html", "北京交管公布的2011年修正居民身份证法", "html", None),
    ("LAW-NPC-GAZETTE-MIRROR", "https://www.pdsxcq.gov.cn/upload/files/2023/6/353f9d72ee36b1751de857cf71917117.pdf", "政府保存的全国人大常委会公报651至654页", "pdf", None),
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fixed_request(url, headers):
    selected = SOURCES + LAW_MIRROR + NATIONAL_LAW
    allowed = {row[1] for row in selected} | {
        f"https://{urlsplit(row[1]).netloc}/robots.txt" for row in selected}
    if url not in allowed:
        raise AccessStopped("固定官方来源范围外的请求已停止", phase="preflight")
    # Use the user's existing proxy settings; preserve TLS and disallow redirects.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(urllib.request.getproxies()), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={
        "User-Agent": BOT + "/0.1 (public-service source review)",
        "Accept-Encoding": "identity", **headers})
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise AccessStopped("响应超过已配置大小限制")
        return response.status, {k.lower(): v for k, v in response.headers.items()}, raw


def collect(*, sources=SOURCES, runtime=RUNTIME):
    runtime.mkdir(parents=True, exist_ok=True)
    # Exclusive creation records intent before any HTTP and prevents duplicate campaigns.
    with (runtime / "attempt.json").open("x", encoding="utf-8") as stream:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat(),
                   "scope": "fixed_public_nanjing_rule_review", "model_calls": 0},
                  stream, ensure_ascii=False)
    store = KnowledgeStore.initialize(runtime / "capture-store")
    snapshot = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                "region": "南京市", "matter": "resident-id-expiry-renewal",
                "model_calls": 0, "human_business_review": False,
                "knowledge_published": False, "sources": []}
    stopped_origins = set()
    for source_id, url, title, kind, published in sources:
        origin = f"https://{urlsplit(url).netloc}"
        record = {"source_id": source_id, "url": url, "title": title,
                  "kind": kind, "page_publication_date_hint": published,
                  "effective_from": None, "requests": []}
        snapshot["sources"].append(record)
        if origin in stopped_origins:
            record["status"] = "skipped_after_origin_stop"
            write(runtime / "snapshot.json", snapshot)
            continue
        checked = datetime.now(timezone.utc).isoformat()
        basis = "Official public facts; attributed development rule review; no applicant data or business approval"
        config = {"id": source_id, "label": title, "enabled": True,
                  "origin": origin, "allowed_paths": [urlsplit(url).path],
                  "seeds": [url], "interval_seconds": 86400, "delay_seconds": 5,
                  "metadata": {"title": title, "region": "national" if source_id.startswith(("NPC", "LAW")) else "南京市",
                    "matter": "resident-id-expiry-renewal", "document_type": "official_public_source",
                    "usage_basis": basis, "scope_confirmed": False},
                  "access_review": {"approved": True, "reviewer": "codex_technical_public_access_screen",
                    "actor_kind": "machine", "checked_at": checked, "usage_basis": basis,
                    "business_review_completed": False}}
        store.register(config)
        fetcher = Fetcher(store)

        def observed(target, headers):
            status, response_headers, raw = fixed_request(target, headers)
            record["requests"].append({"url": target, "http_status": status,
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            write(runtime / "snapshot.json", snapshot)
            if urlsplit(target).path == "/robots.txt":
                (runtime / (source_id + "-robots.txt")).write_bytes(raw)
            return status, response_headers, raw

        fetcher._request = observed
        try:
            status, response_headers, raw, final_url = fetcher.fetch(config, url)
            record["checked_at"] = datetime.now(timezone.utc).isoformat()
            if status != 200 or final_url != url:
                raise AccessStopped("没有取得固定来源的HTTP200正文", phase="document", http_status=status)
            if kind == "pdf" and not raw.startswith(b"%PDF-"):
                raise ValueError("PDF原件内容类型不匹配")
            if kind == "html" and b"<html" not in raw[:10000].lower() and b"<!doctype" not in raw[:10000].lower():
                raise ValueError("HTML原件内容类型不匹配")
            filename = source_id + "." + kind
            (runtime / filename).write_bytes(raw)
            record.update(status="captured_pending_review", filename=filename,
                original_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                content_type=response_headers.get("content-type"))
        except (OSError, ValueError) as error:
            record.update(status="stopped", error_type=type(error).__name__)
            if isinstance(error, AccessStopped):
                record["access_failure"] = error.detail()
            stopped_origins.add(origin)
        write(runtime / "snapshot.json", snapshot)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="南京规则核查官方原件的一次性有界采集")
    parser.add_argument("--collect", action="store_true", help="首次采集；拒绝覆盖或重复请求")
    parser.add_argument("--group", choices=("nanjing", "law-mirror", "national-law"), default="nanjing",
                        help="固定来源组；每组仅允许一次采集，不重试已停止来源")
    args = parser.parse_args()
    sources, runtime = {"nanjing": (SOURCES, RUNTIME),
        "law-mirror": (LAW_MIRROR, RUNTIME.parent / "nanjing-law-mirror-v1"),
        "national-law": (NATIONAL_LAW, RUNTIME.parent / "nanjing-national-law-v1")}[args.group]
    if args.collect:
        value = collect(sources=sources, runtime=runtime)
    else:
        value = json.loads((runtime / "snapshot.json").read_text(encoding="utf-8"))
    print(json.dumps({"sources": [{"source_id": s["source_id"], "status": s["status"],
        "bytes": s.get("bytes"), "requests": len(s["requests"])} for s in value["sources"]],
        "model_calls": value["model_calls"], "knowledge_published": value["knowledge_published"]},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Exercise diagnostic provenance using isolated synthetic transports, never real sources."""

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from starlette.testclient import TestClient

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import AccessStopped, Fetcher, schedule, worker_once
from gov_mcp.knowledge_web import create_app
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def canonical_binding():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    with store.db() as db:
        return {"counts": {table: db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
                           for table in ("sites", "documents", "revisions", "chunks", "reviews", "snapshot_evaluations", "versions", "heads")},
                "source_configs": fingerprint([list(r) for r in db.execute("SELECT id,config,paused_reason FROM sites ORDER BY id")]),
                "policy": fingerprint((store.root / "publication-policy.json").read_bytes())}


def main():
    parser = argparse.ArgumentParser(description="隔离模拟来源的停止诊断核对；不请求任何外部网站")
    parser.add_argument("--fixture-directory", required=True, type=Path)
    parser.add_argument("--serve-port", type=int)
    args = parser.parse_args()
    target = args.fixture_directory.resolve()
    if target.exists() or not target.is_relative_to((RUNTIME / "knowledge-update/diagnostic-fixtures").resolve()):
        raise ValueError("Use a new directory inside the private diagnostic-fixtures folder")
    if args.serve_port is not None and not 1024 <= args.serve_port <= 65535:
        raise ValueError("Bounded unprivileged loopback port required")
    before = canonical_binding()
    ensure_private_directory(target)
    store = KnowledgeStore.initialize(target)
    requests = []
    labels = {"robots": "模拟：robots检查403", "document": "模拟：文件访问401",
              "unknown": "模拟：未提供阶段的停止", "legacy": "模拟：旧记录未保存阶段"}
    for key, label in labels.items():
        origin = f"https://{key}.example.gov.cn"
        store.register({"id": key, "label": label, "origin": origin, "allowed_paths": ["/docs/"],
            "seeds": [origin+"/docs/guide"], "enabled": True, "interval_seconds": 3600, "delay_seconds": 5,
            "metadata": {"title": label, "region": "郑州市二七区", "matter": "demo-id-renewal",
                         "document_type": "synthetic", "usage_basis": "Controlled engineering fixture only; no external requests"},
            "access_review": {"approved": True, "reviewer": "Codex synthetic transport verification", "actor_kind": "synthetic",
                "checked_at": datetime.now(timezone.utc).isoformat(), "usage_basis": "Synthetic transport; never a real public source"}})
    schedule(store)
    fetcher = Fetcher(store)
    def synthetic_request(url, headers):
        parsed = urlsplit(url)
        requests.append({"host": parsed.hostname, "path": parsed.path})
        if parsed.path == "/robots.txt":
            return (403, {}, b"denied") if parsed.hostname.startswith("robots.") else (404, {}, b"")
        return 401, {}, b"denied"
    fetcher._request = synthetic_request
    class UnknownFetcher:
        def fetch(self, config, url):
            raise AccessStopped("Access denied; source paused")
    # The only clock override removes wait time from synthetic transport reservations.
    with patch("gov_mcp.knowledge_fetch.time.sleep", lambda _: None):
        worker_once(store, fetcher, owner="controlled-diagnostic")
        worker_once(store, fetcher, owner="controlled-diagnostic")
        worker_once(store, UnknownFetcher(), owner="controlled-diagnostic")
    with store.db() as db:
        db.execute("UPDATE jobs SET state='failed',error='Access denied; source paused' WHERE site_id='legacy'")
        db.execute("UPDATE sites SET failures=1,paused_reason='Access denied; source paused' WHERE id='legacy'")
        store.log(db, "check_failed", {"site": "legacy", "reason": "Access denied; source paused"})
    with TestClient(create_app(target)) as client:
        api = client.get("/api/status")
        status = api.json()
        ui = client.get("/")
    sources = {s["id"]: s for s in status["sites"]}
    request_count = len(requests)
    no_more_work = not worker_once(store, fetcher, owner="controlled-diagnostic")
    with store.db() as db:
        plan = " ".join(str(r["detail"]) for r in db.execute("EXPLAIN QUERY PLAN SELECT at,data FROM audit "
            "WHERE action='check_failed' AND json_extract(data,'$.site')=? ORDER BY id DESC LIMIT 1", ("robots",)))
    after = canonical_binding()
    checks = {"robots403_no_document_request": sources["robots"]["access_stop"]["phase"] == "robots"
              and sources["robots"]["access_stop"]["http_status"] == 403
              and [r["path"] for r in requests if r["host"].startswith("robots.")] == ["/robots.txt"],
              "file401_is_document_phase": sources["document"]["access_stop"]["phase"] == "document"
              and sources["document"]["access_stop"]["http_status"] == 401,
              "unknown_details_not_inferred_from_error_text": sources["unknown"]["access_stop"]["phase"] == "unknown"
              and sources["unknown"]["access_stop"]["http_status"] is None and sources["legacy"]["access_stop"] is None,
              "all_four_paused_and_not_retried": all(s["paused_reason"] for s in sources.values()) and no_more_work and request_count == len(requests),
              "status_and_workbench_available": api.status_code == ui.status_code == 200 and "robots访问检查未通过" in ui.text,
              "failure_lookup_uses_partial_index": "source_check_failures" in plan,
              "no_fixture_document_or_publication": all(status["counts"][k] == 0 for k in ("documents", "revisions", "heads", "versions")),
              "canonical_candidates_sources_and_policy_unchanged": before == after}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
              "transport": "synthetic in-process responses only; no external network request", "actual_external_request_count": 0,
              "synthetic_request_paths": requests, "source_diagnostics": [{"id": k, "access_stop": sources[k]["access_stop"]} for k in labels],
              "canonical_binding": after, "business_approval_performed": False,
              "modules": {name: fingerprint((ROOT / "src/gov_mcp" / name).read_bytes()) for name in ("knowledge.py", "knowledge_fetch.py", "knowledge_workbench.html")}}
    (ROOT / "evidence/knowledge-update/access-stop-diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks}, ensure_ascii=False), flush=True)
    if not report["passed"]:
        raise SystemExit(1)
    if args.serve_port is not None:
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_app(target), host="127.0.0.1", port=args.serve_port,
                                               log_level="warning", timeout_graceful_shutdown=2))
        timer = threading.Timer(180, lambda: setattr(server, "should_exit", True))
        timer.daemon = True
        timer.start()
        print(json.dumps({"fixture_url": f"http://127.0.0.1:{args.serve_port}", "pid": os.getpid(), "maximum_seconds": 180}), flush=True)
        try:
            server.run()
        finally:
            timer.cancel()


if __name__ == "__main__":
    main()

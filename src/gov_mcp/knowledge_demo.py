"""Self-contained, offline engineering demo; never initializes a business runtime."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, encode
from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge_tools import KnowledgeTools


MODE = "synthetic_team_demo_v1"
WARNING = "离线工程演示：全部资料为虚构，不可用于实际办事；没有联网抓取或大模型调用。"
REGION = "工程演示区"
MATTER = "demo-id-renewal"
SOURCE = "SYNTHETIC-TEAM-DEMO"
URL = "https://team-demo.example.invalid/docs/guide"
POLICY = {"schema": 1, "mode": "engineering_fixture"}
QUESTION = "到期换领模拟材料和费用有哪些？"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(encode(value) + "\n", encoding="utf-8")


def fixture(number):
    material = "模拟材料甲" if number == 1 else "模拟材料乙"
    amount = "二十" if number == 1 else "三十"
    return (f'<html lang="zh-CN"><div id="demo">'
            f'<p>到期换领演示材料：本人提交{material}。全部为虚构工程资料。</p>'
            f'<p>到期换领演示费用：虚构收费{amount}个演示积分，不代表人民币费用。</p>'
            '</div></html>').encode("utf-8")


def demo_store(runtime):
    root = Path(runtime).resolve()
    marker = read_json(root / "team-demo.json")
    if marker.get("mode") != MODE or read_json(root / "publication-policy.json") != POLICY:
        raise ValueError("该目录不是独立的虚构演示库，拒绝操作。")
    store = KnowledgeStore(root)
    with store.db() as db:
        sources = {r[0] for r in db.execute("SELECT id FROM sites")}
        if sources != {SOURCE}:
            raise ValueError("演示目录包含其他来源，拒绝操作。")
        for row in db.execute("SELECT metadata FROM revisions"):
            metadata = json.loads(row[0])
            if (metadata.get("document_type"), metadata.get("region"), metadata.get("matter")) != ("synthetic", REGION, MATTER):
                raise ValueError("演示目录混入了非演示资料，拒绝操作。")
    return store, marker


def ingest_fixture(store, number):
    raw = fixture(number)
    chunks, _ = parse_html(raw, {"id": "demo"})
    for chunk in chunks:
        chunk["category"] = "materials" if "演示材料" in chunk["text"] else "fees"
    metadata = {"title": f"虚构工程演示指南 v{number}", "region": REGION, "matter": MATTER,
                "document_type": "synthetic", "published_at": f"2020-01-0{number}",
                "effective_from": f"2020-01-0{number}", "effective_to": None,
                "scope_confirmed": True, "usage_basis": "项目自制虚构演示资料，仅限独立演示库。"}
    revision = store.ingest(SOURCE, URL, raw, metadata, chunks)
    # This is explicitly a local fixture observation, never an HTTP-success claim.
    with store.db() as db:
        db.execute("UPDATE sites SET last_success=? WHERE id=?", (time.time(), SOURCE))
        store.log(db, "demo_local_fixture_replay", {"revision": revision, "network_requests": 0})
    return revision


def initialize(runtime):
    root = Path(runtime).resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("初始化要求空目录；已有演示请运行 check，不会覆盖现有文件。")
    for ancestor in root.parents:
        if (ancestor / "phase.json").exists():
            raise ValueError("演示库不能建在已有业务运行目录内部。")
    store = KnowledgeStore.initialize(root)
    write_json(root / "publication-policy.json", POLICY)
    marker = {"mode": MODE, "warning": WARNING, "versions": [], "network_requests": 0,
              "model_requests": 0, "business_approval": False,
              "collection_mode": "local_synthetic_html_replay"}
    write_json(root / "team-demo.json", marker)
    write_json(root / "knowledge-scope.json", {"region": REGION, "matter": MATTER,
               "included_branch": "虚构的成年本人到期换领工程演示"})
    write_json(root / "knowledge-role-map.json", {"team-demo": {
        "citizen": {"role": "citizen"}, "staff": {"role": "staff"}}})
    store.register({"id": SOURCE, "label": "虚构演示来源，不进行网络访问", "origin": "https://team-demo.example.invalid",
        "enabled": True, "allowed_paths": ["/docs/"], "seeds": [URL], "interval_seconds": 86400,
        "delay_seconds": 5, "content_selector": {"id": "demo"}, "discover_links": False,
        "metadata": {"document_type": "synthetic"},
        "access_review": {"approved": True, "actor_kind": "synthetic", "reviewer": "synthetic-demo-runner",
                          "usage_basis": "仅重放项目自制HTML；不请求此虚构域名。",
                          "checked_at": datetime.now(timezone.utc).isoformat()}})
    revision = ingest_fixture(store, 1)
    store.review(revision, "synthetic-demo-runner", confirmed=True)
    version = store.candidate(REGION, MATTER)
    store.publish(version, "synthetic-demo-runner", confirmed=True)
    marker["versions"].append({"number": 1, "revision": revision, "version": version})
    write_json(root / "team-demo.json", marker)
    return check(root)


def query(runtime, role, question=QUESTION, version=None):
    store, marker = demo_store(runtime)
    if role not in ("citizen", "staff"):
        raise ValueError("演示角色只能选择 citizen 或 staff。")
    if version and version not in {v["version"] for v in marker["versions"]}:
        raise ValueError("只能查询当前演示库中的已发布演示版本。")
    result = KnowledgeTools(store.root).execute("search_evidence", "team-demo", role,
                                               query=question, knowledge_version=version)
    return {"demo": True, "warning": WARNING, "result": result}


def update(runtime):
    store, marker = demo_store(runtime)
    if len(marker["versions"]) != 1:
        raise ValueError("v2演示更新已经完成；请运行 check，或选择新的空目录重新演示。")
    revision = ingest_fixture(store, 2)
    pending = query(store.root, "citizen")["result"]
    if pending["message_code"] != "UPDATE_PENDING_REVIEW" or pending["citations"]:
        raise ValueError("新资料待核时未能停止答复，未继续演示发布。")
    store.review(revision, "synthetic-demo-runner", confirmed=True)
    version = store.candidate(REGION, MATTER)
    store.publish(version, "synthetic-demo-runner", confirmed=True)
    marker["versions"].append({"number": 2, "revision": revision, "version": version})
    marker["pending_update_stop_verified"] = True
    write_json(store.root / "team-demo.json", marker)
    return check(store.root)


def refresh(runtime):
    store, marker = demo_store(runtime)
    with store.db() as db:
        for entry in marker["versions"]:
            store._verify(db, entry["revision"])
    revision = ingest_fixture(store, marker["versions"][-1]["number"])
    if revision != marker["versions"][-1]["revision"]:
        raise ValueError("演示资料与版本记录不一致，拒绝沿用检查结果。")
    return check(store.root)


def check(runtime):
    store, marker = demo_store(runtime)
    answers = {role: query(store.root, role)["result"] for role in ("citizen", "staff")}
    expected = "模拟材料甲" if len(marker["versions"]) == 1 else "模拟材料乙"
    checks = {"demo_only": marker["business_approval"] is False,
              "both_roles_have_literal_citations": all(a["status"] == "OK" and a["citations"] and
                  all(c["original_sha256"] and c["locator"] and c["effective_from"] for c in a["citations"]) for a in answers.values()),
              "latest_version_used": all(a["knowledge_version"] == marker["versions"][-1]["version"] and
                  expected in a["data"]["answer"] for a in answers.values()),
              "unmapped_role_rejected": KnowledgeTools(store.root).execute("get_environment_status", "team-demo", "unknown")["message_code"] == "ROLE_NOT_CONFIGURED"}
    with store.db() as db:
        for entry in marker["versions"]:
            row, _ = store._verify(db, entry["revision"])
            checks[f"original_v{entry['number']}_intact"] = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes() == fixture(entry["number"])
    materials = store.search("模拟材料", REGION, MATTER, category="materials")
    checks["category_filter_works"] = bool(materials["citations"]) and all(c["category"] == "materials" for c in materials["citations"])
    if len(marker["versions"]) == 2:
        old = query(store.root, "staff", version=marker["versions"][0]["version"])["result"]
        checks["pending_update_stopped"] = marker.get("pending_update_stop_verified") is True
        checks["old_version_preserved"] = old["status"] == "OK" and old["data"]["historical_snapshot"] is True and "模拟材料甲" in old["data"]["answer"]
    report = {"mode": MODE, "warning": WARNING, "checks": checks, "passed": all(checks.values()),
              "counts": store.status()["counts"], "versions": marker["versions"],
              "network_requests": 0, "model_requests": 0, "business_approval": False,
              "heartbeat_registered": False, "answers": answers}
    write_json(store.root / "demo-check.json", report)
    if not report["passed"]:
        raise ValueError("演示自检未通过，请查看演示目录内 demo-check.json。")
    return report


def create_app(runtime):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Route

    store, marker = demo_store(runtime)

    async def home(request):
        return HTMLResponse((Path(__file__).parent / "knowledge_demo.html").read_text(encoding="utf-8"),
            headers={"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'", "X-Content-Type-Options": "nosniff"})

    async def health(request):
        return JSONResponse({"mode": MODE, "warning": WARNING, "status": "UP"})

    async def search(request):
        try:
            return JSONResponse(query(store.root, request.query_params.get("role", "citizen"),
                request.query_params.get("q", QUESTION), request.query_params.get("version")))
        except (ValueError, OSError) as error:
            return JSONResponse({"demo": True, "warning": WARNING, "error": str(error)}, status_code=400)

    async def status(request):
        _, current = demo_store(store.root)
        return JSONResponse({"demo": True, "warning": WARNING, "counts": store.status()["counts"], "versions": current["versions"]})

    return Starlette(routes=[Route("/", home), Route("/health", health), Route("/api/query", search), Route("/api/status", status)],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])])


def main():
    parser = argparse.ArgumentParser(description=WARNING)
    parser.add_argument("action", choices=["init", "check", "update", "refresh", "query", "serve"])
    parser.add_argument("--runtime", type=Path, required=True, help="独立演示目录；初始化要求空目录。")
    parser.add_argument("--role", choices=["citizen", "staff"], default="citizen")
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--port", type=int, default=18772)
    args = parser.parse_args()
    try:
        if args.action == "serve":
            import uvicorn
            if not 1 <= args.port <= 65535:
                raise ValueError("端口必须在1至65535之间。")
            print(WARNING, flush=True)
            print(f"演示网址：http://127.0.0.1:{args.port}；在此终端按 Ctrl+C 停止。", flush=True)
            uvicorn.run(create_app(args.runtime), host="127.0.0.1", port=args.port, log_level="warning")
            return
        result = (query(args.runtime, args.role, args.question) if args.action == "query" else
                  {"init": initialize, "check": check, "update": update, "refresh": refresh}[args.action](args.runtime))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"演示操作失败：{error}\n")


if __name__ == "__main__":
    main()

"""Authenticated maintenance workbench and narrowly scoped MCP tools."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
import uvicorn

from gov_mcp.core import result
from gov_mcp.server import BearerGate
from .engine import Engine
from .native import NativeDriver, release_local
from .replay import SyntheticReplay
from .store import KBError, Store
from .worker import Worker


def create_app(engine, driver, token, roles, *, config_url="http://host.docker.internal:5010", authenticator=None, start_worker=True):
    sessions, store = {}, engine.store
    cookie_name = "govkb_demo_session" if engine.synthetic else "govkb_session"
    worker = Worker(engine, driver)
    security_settings = TransportSecuritySettings(allowed_hosts=["localhost:*", "127.0.0.1:*", "govkb-maintenance:8775", "govkb-demo:8775"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    security = TransportSecurityMiddleware(security_settings)
    mcp = FastMCP("govkb_maintenance", transport_security=security_settings)

    def role(user):
        return roles.get(user.get("tenant_id"), {}).get(user.get("user_id"))

    def scoped(run_id, tenant_id, user_id):
        if role({"tenant_id": tenant_id, "user_id": user_id}) not in {"service", "both"}: raise KBError("FORBIDDEN")
        return engine.context(run_id)

    def execute(name, run_id, tenant_id, user_id, action):
        try:
            scoped(run_id, tenant_id, user_id)
            value = result("OK", "MAINTENANCE_STAGE_COMPLETE", **action())
        except KBError as error:
            value = result("BLOCKED", str(error))
        except Exception:
            value = result("ERROR", "MAINTENANCE_STAGE_UNAVAILABLE")
        store.audit("mcp", {"tool": name, "run_id": run_id, "status": value["status"], "message_code": value["message_code"],
            "actor": hashlib.sha256((tenant_id+"\0"+user_id).encode()).hexdigest()})
        return value

    @mcp.tool()
    def govkb_get_change_batch(run_id: str, tenant_id: str, user_id: str, batch_id: str | None = None) -> dict:
        """Read only this leased run. Omit batch_id for controller summaries; specify it for child evidence."""
        def action():
            run, value = engine.context(run_id, batch_id)
            if batch_id:
                candidate = store.get("candidates", value["candidate_id"]) if value["candidate_id"] else None
                return {"batch": {k: value[k] for k in ("id", "source_id", "stage", "assets", "candidate_id")},
                    "allowed_kinds": list(engine.ontology()["entity_types"]),
                    "classification_labels": [{"segment_id": r["segment_id"], "routes": r["routes"], "purposes": r["purposes"]} for r in (value["classification"] or {}).get("records", [])],
                    "candidate": candidate}
            return {"batches": [{"id": j["id"], "source_id": j["source_id"], "stage": j["stage"], "candidate_id": j["candidate_id"],
                "segment_count": sum(len(a["segments"]) for a in j["assets"])} for j in value]}
        return execute("govkb_get_change_batch", run_id, tenant_id, user_id, action)

    @mcp.tool()
    def govkb_classify_changes(run_id: str, batch_id: str, tenant_id: str, user_id: str) -> dict:
        """Classify this batch through Jev. No web discovery or business-rule publishing."""
        def action():
            value = engine.classify(run_id, batch_id)
            release_local(engine.config, "controller")
            return {"classification_summary": value["summary"]}
        return execute("govkb_classify_changes", run_id, tenant_id, user_id, action)

    @mcp.tool()
    def govkb_submit_candidates(run_id: str, batch_id: str, candidates: list[dict], tenant_id: str, user_id: str,
                                ontology_proposals: list[dict] | None = None) -> dict:
        """Submit verbatim supported claims: kind,name,quote,source_segment_ids. Never publish rules."""
        return execute("govkb_submit_candidates", run_id, tenant_id, user_id,
            lambda: {"candidate": engine.submit(run_id, batch_id, candidates, ontology_proposals)})

    @mcp.tool()
    def govkb_validate_candidates(run_id: str, candidate_id: str, review: dict, tenant_id: str, user_id: str) -> dict:
        """Child review lists omissions,conflicts,notes. Combine it with deterministic checks and Jev support signals."""
        def action():
            value = engine.validate(run_id, candidate_id, review)
            release_local(engine.config, "reviewer")
            return {"validation": value}
        return execute("govkb_validate_candidates", run_id, tenant_id, user_id, action)

    @mcp.tool()
    def govkb_finish_run(run_id: str, batch_id: str, summary: str, tenant_id: str, user_id: str) -> dict:
        """Mark one validated batch processed. Only worker can auto-activate document versions; humans publish rules."""
        return execute("govkb_finish_run", run_id, tenant_id, user_id, lambda: engine.finish_job(run_id, batch_id, summary))

    @mcp.tool()
    def govkb_search_evidence(query: str, tenant_id: str, user_id: str, kind: str = "documents", version: str | None = None) -> dict:
        """Read a pinned document/rule version. Rules contain only human-approved, current-source claims."""
        if role({"tenant_id":tenant_id,"user_id":user_id}) not in {"service","maintainer","both"}:
            return result("BLOCKED", "FORBIDDEN")
        try:
            found = engine.search(query, kind, version)
            value = result(found["status"], "KNOWLEDGE_FOUND" if found["items"] else "NO_SUPPORTING_EVIDENCE")
            value["knowledge_version"] = found["version"]
            value["items"] = found["items"]
            value["citations"] = [{"source_id":item["source_id"],"segment_id":item.get("segment_id"),
                "source_segment_ids":item.get("source_segment_ids"),"locator":item.get("locator"),"source_url":item.get("source_url"),
                "knowledge_version":found["version"]} for item in found["items"]]
            lines=[found.get("notice", "资料不足，未形成办理结论。")]
            for item,citation in zip(found["items"],value["citations"]):
                quote=re.sub(r"([\\`*_{}\[\]<>])",r"\\\1",item.get("content",item.get("quote",""))).replace("\n"," ")
                citation['url'] = ('http://127.0.0.1:8778' if engine.synthetic else 'http://127.0.0.1:8775')+'/api/original/'+item.get('evidence_version',found['version'])+'/'+item['raw_sha256']
                lines.append("- "+quote+" [查看此版本原文]("+citation['url']+")")
            if found["version"]: lines.append("资料版本：`"+found["version"]+"`。")
            value["data"]["answer_markdown"]="\n\n".join(lines)
            store.audit("query",{"kind":kind,"version":found["version"],"query_sha256":hashlib.sha256(query.encode()).hexdigest()})
            return value
        except Exception as error:
            return result("ERROR",str(error) if isinstance(error,KBError) else "QUERY_UNAVAILABLE",answer_markdown="知识查询未完成，请检查工作台状态。")

    async def identity(request):
        entry = sessions.get(request.cookies.get(cookie_name))
        if not entry or entry["expires"] < time.monotonic(): return None
        if authenticator: return entry["user"]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.get(config_url+"/user/current_user_info", headers={"Authorization": "Bearer "+entry["token"]})
                response.raise_for_status()
                user = response.json()["data"]["user"]
                return user if role(user) in {"maintainer", "both"} else None
        except (httpx.HTTPError, KeyError, ValueError): return None

    async def login(request):
        try:
            body = await request.json()
            if set(body) != {"email", "password"} or not all(isinstance(v,str) and 0<len(v)<1024 for v in body.values()): raise KBError("INVALID_LOGIN")
            if authenticator:
                user = authenticator(body)
                token_value = None
            else:
                async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
                    response = await client.post(config_url+"/user/signin", json=body)
                    response.raise_for_status()
                    token_value = response.json()["data"]["session"]["access_token"]
                    response = await client.get(config_url+"/user/current_user_info", headers={"Authorization": "Bearer "+token_value})
                    response.raise_for_status()
                    user = response.json()["data"]["user"]
            if not user or role(user) not in {"maintainer", "both"}: raise KBError("FORBIDDEN")
            key = secrets.token_urlsafe(32)
            # Prune expired sessions; credentials are never persisted here.
            for old in list(sessions):
                if sessions[old]["expires"] < time.monotonic(): sessions.pop(old, None)
            sessions[key] = {"token": token_value, "user": user, "expires": time.monotonic()+1800}
            response = JSONResponse({"status": "OK"})
            response.set_cookie(cookie_name, key, httponly=True, samesite="strict", max_age=1800)
            return response
        except Exception:
            return JSONResponse({"status": "BLOCKED", "message_code": "LOGIN_FAILED"}, status_code=401)

    async def logout(request):
        sessions.pop(request.cookies.get(cookie_name), None)
        response = JSONResponse({"status": "OK"})
        response.delete_cookie(cookie_name)
        return response

    async def state(request):
        if not await identity(request): return JSONResponse({"message_code": "LOGIN_REQUIRED"}, status_code=401)
        return JSONResponse({"heartbeat": store.get("state", "heartbeat"), "paused": bool(store.get("state", "paused")),
            "demo_mode": engine.config["demo_mode"], "interval": engine.config["demo_interval_seconds"] if engine.config["demo_mode"] else engine.config["check_interval_seconds"],
            "active_documents": store.get("state", "active_documents"), "active_rules": store.get("state", "active_rules"),
            "models": engine.config["models"], "sources": store.rows("sources"), "configured_sources": engine.config["sources"],
            "runs": store.rows("runs")[:30], "jobs": [{k:j.get(k) for k in ("id", "source_id", "status", "stage", "error", "attempts")} for j in store.rows("jobs")[:100]],
            "candidates": store.rows("candidates")[:100], "versions": [{k:v[k] for k in ("id", "kind", "base", "created_at")} for v in store.rows("versions")],
            "provider": engine.provider.name, "synthetic_mode": engine.synthetic}, headers={"Cache-Control": "no-store"})

    async def operation(request):
        user = await identity(request)
        if not user: return JSONResponse({"message_code": "LOGIN_REQUIRED"}, status_code=401)
        name = request.path_params["name"]
        try:
            body = await request.json()
            if not isinstance(body, dict): raise KBError("INVALID_ARGUMENTS")
            actor = hashlib.sha256((user["tenant_id"]+"\0"+user["user_id"]).encode()).hexdigest()
            allowed = {"check": set(), "pause": set(), "resume": set(), "demo": {"enabled"}, "review": {"candidate_id", "expected_base", "acknowledge", "accept"},
                "recheck": {"candidate_id"}, "rollback": {"kind", "version", "expected_base"}, "fixture": {"html"}}
            if name not in allowed or set(body)-allowed[name]: raise KBError("INVALID_ARGUMENTS")
            value = None
            if name == "check": store.put("state", "trigger", secrets.token_hex(8))
            elif name in {"pause", "resume"}: store.put("state", "paused", name == "pause")
            elif name == "demo":
                if type(body.get("enabled")) is not bool: raise KBError("INVALID_ARGUMENTS")
                engine.config["demo_mode"] = body["enabled"]
                store.put("state", "demo_mode", body["enabled"])
            elif name == "review":
                if type(body.get("accept")) is not bool or type(body.get("acknowledge")) is not bool: raise KBError("INVALID_ARGUMENTS")
                value = await asyncio.to_thread(engine.review_candidate, body["candidate_id"], body.get("expected_base"), actor,
                    accept=body["accept"], acknowledge=body["acknowledge"])
            elif name == "recheck":
                engine.requeue_candidate(body["candidate_id"])
                store.put("state", "trigger", secrets.token_hex(8))
            elif name == "rollback": store.rollback(body["kind"], body["version"], body.get("expected_base"), actor)
            elif name == "fixture":
                if not engine.synthetic or not isinstance(body.get("html"),str) or len(body["html"])>12000: raise KBError("FIXTURE_DISABLED")
                store.put("state", "fixture_html", body["html"])
            store.audit("maintainer_"+name, {"actor": actor, "candidate_id": body.get("candidate_id"), "version": value})
            return JSONResponse({"status": "OK", "version": value})
        except KBError as error:
            return JSONResponse({"status": "BLOCKED", "message_code": str(error)}, status_code=409)
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"status": "BLOCKED", "message_code": "INVALID_ARGUMENTS"}, status_code=400)

    async def search(request):
        if not await identity(request): return JSONResponse({"message_code": "LOGIN_REQUIRED"}, status_code=401)
        try:
            value = await asyncio.to_thread(engine.search, request.query_params.get("q", ""), request.query_params.get("kind", "documents"), request.query_params.get("version"))
            return JSONResponse(value)
        except Exception as error:
            return JSONResponse({"status": "ERROR", "message_code": str(error) if isinstance(error, KBError) else "QUERY_UNAVAILABLE"}, status_code=503)

    async def original(request):
        if not await identity(request): return JSONResponse({"message_code": "LOGIN_REQUIRED"}, status_code=401)
        try:
            version = store.version(request.path_params["version"])
            asset = next(a for a in version["data"]["assets"] if a["sha256"] == request.path_params["sha"])
            raw = store.read_object(asset["sha256"])
            fmt = asset["format"]
            return Response(raw, media_type="image/"+("jpeg" if fmt in {"jpg","jpeg"} else fmt) if fmt in {"png","jpg","jpeg"} else "text/plain; charset=utf-8",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})
        except (KBError, KeyError, StopIteration): return JSONResponse({"message_code": "ORIGINAL_NOT_FOUND"}, status_code=404)

    async def fixture(request):
        if not engine.synthetic: return Response(status_code=404)
        return HTMLResponse(store.get("state", "fixture_html") or '<html><nav>模拟导航</nav><article id="policy"><p>模拟身份证事项需提交申请表。</p></article></html>',
            headers={"Content-Security-Policy": "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                     "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def home(request):
        return HTMLResponse(Path(__file__).with_name("workbench.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    async def health(request):
        return JSONResponse({"status": "UP", "service": "govkb_maintenance", "heartbeat": store.get("state", "heartbeat")})

    app = mcp.sse_app()
    @asynccontextmanager
    async def lifespan(app):
        if start_worker: worker.start()
        yield
        if start_worker: worker.stop()
    app.router.lifespan_context = lifespan
    app.router.routes[:0] = [Route("/", home), Route("/health", health), Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]), Route("/api/state", state), Route("/api/actions/{name}", operation, methods=["POST"]),
        Route("/api/search", search), Route("/api/original/{version}/{sha}", original), Route("/fixture/policy", fixture)]
    mcp_gate = BearerGate(app, token, security)
    async def gated(scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            if scope["path"].startswith(("/sse", "/messages")):
                return await mcp_gate(scope, receive, send)
            rejected = await security.validate_request(request, is_post=scope["method"] == "POST")
            if rejected is not None: return await rejected(scope, receive, send)
            if scope["method"] == "POST" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
                return await JSONResponse({"message_code": "ORIGIN_REJECTED"}, status_code=403)(scope, receive, send)
        await app(scope, receive, send)
    return gated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8775)
    args = parser.parse_args()
    root = args.runtime
    store = Store(root)
    config = json.loads((root / "heartbeat.json").read_text(encoding="utf-8"))
    classification = json.loads((root / "classification.json").read_text(encoding="utf-8"))
    if (root / "providers.env").exists():
        from dotenv import dotenv_values
        values=dotenv_values(root / "providers.env")
        allowed={"TYPESAFE_API_KEY",config["models"]["embedding"].get("api_key_env", "")}
        for key in allowed-{""}:
            if not os.environ.get(key) and values.get(key): os.environ[key]=values[key]
    synthetic = os.environ.get("GOVKB_SYNTHETIC_REPLAY") == "1"
    if synthetic and any(s.get("data_class") != "synthetic" for s in config["sources"]): raise ValueError("Replay runtime only accepts synthetic sources")
    provider = SyntheticReplay() if synthetic else None
    engine = Engine(store, config, classification, provider=provider, synthetic=synthetic)
    if store.get("state", "paused") is None: store.put("state", "paused", bool(config.get("start_paused")))
    if store.get("state", "demo_mode") is not None: engine.config["demo_mode"] = store.get("state", "demo_mode")
    driver = NativeDriver(root)
    token = (root / "mcp-token.txt").read_text(encoding="ascii").strip()
    if len(token)<32: raise ValueError("Dedicated bearer token required")
    roles = json.loads((root / "roles.json").read_text(encoding="utf-8"))
    uvicorn.run(create_app(engine, driver, token, roles), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__": main()

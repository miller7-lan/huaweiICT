"""Loopback-only synthetic workbench, role-scoped HTTP and local MCP adapter."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import secrets
import re
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Mount
import uvicorn

from .workflow import Workflow, WorkflowError, ROOT
from .service import DecisionError
from .models import canonical_hash
from gov_mcp.knowledge.store import KBError


def create_app(workflow, professional_token, mcp_token, *, start_worker=True):
    sessions = {}
    mcp = FastMCP("gov_decision_engineering", stateless_http=True, json_response=True, streamable_http_path="/",
        transport_security=TransportSecuritySettings(allowed_hosts=["localhost:*", "127.0.0.1:*", "host.docker.internal:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*", "http://host.docker.internal:*"]))

    @mcp.tool()
    def govkb_open_query(facts: dict, branch_id: str, question: str = "") -> dict:
        """Pin a staged query; return a task ID for native managed child agents."""
        return workflow.open_query(facts, branch_id, question)

    @mcp.tool()
    def govkb_retrieve_query(task_id: str) -> dict:
        """Retriever child: read one bounded round from registered pinned evidence."""
        return workflow.retrieve_query(task_id)

    @mcp.tool()
    def govkb_verify_query(task_id: str) -> dict:
        """Verifier child: exact citation and three-valued rule checks; no publication."""
        return workflow.verify_query(task_id)

    @mcp.tool()
    def govkb_finalize_query(task_id: str) -> dict:
        """Controller: return only a verified answer after the current suspension gate."""
        return workflow.finalize_query(task_id)

    @mcp.tool()
    def govkb_query_state(task_id: str) -> dict:
        """Read persisted stage after a managed child returns; never trust its prose."""
        task = workflow.get("query", task_id)
        if not task or not task.get("staged"): raise WorkflowError("STAGED_QUERY_REQUIRED")
        return {"task_id": task_id, "status": task["status"], "rounds": len(task["rounds"]), "version_id": task["version_id"]}

    @mcp.tool()
    def govkb_query(facts: dict, branch_id: str, question: str = "") -> dict:
        """Run a synthetic, source-linked query. No publication permission."""
        return workflow.query(facts, branch_id, question=question)

    @mcp.tool()
    def govkb_get_change_batch(run_id: str) -> dict:
        """Read immutable inputs and persisted outputs for a worker run."""
        return {"packages": [p for p in workflow.rows("package") if p["run_id"] == run_id]}

    def batch(batch_id):
        value = workflow.get("package", batch_id)
        if not value: raise WorkflowError("PACKAGE_NOT_FOUND")
        return value

    @mcp.tool()
    def govkb_classify_changes(batch_id: str) -> dict:
        """Read the worker's idempotent classification sidecar (explicit fixture replay)."""
        p = batch(batch_id)
        return {"classification": p["classification"], "stage": p["stage"]}

    @mcp.tool()
    def govkb_submit_candidates(batch_id: str, candidates: list[dict]) -> dict:
        """Acknowledge EXACT replay candidates only; model-origin edits cannot bypass review."""
        p = batch(batch_id)
        if candidates != p["candidates"]: raise WorkflowError("CANDIDATE_SCOPE_MISMATCH")
        return {"candidate_id": batch_id, "status": "already_persisted", "formal_publication": False}

    @mcp.tool()
    def govkb_validate_candidates(candidate_id: str) -> dict:
        """Read deterministic validation and offline support signals; never publish."""
        p = batch(candidate_id)
        return {"validation": p.get("validation"), "stage": p["stage"]}

    @mcp.tool()
    def govkb_finish_run(run_id: str, summary: str) -> dict:
        """Record a bounded service summary; cannot activate business knowledge."""
        if not isinstance(summary, str) or not 1 <= len(summary) <= 1000: raise WorkflowError("INVALID_SUMMARY")
        run = workflow.store.get("runs", run_id)
        if not run or run["status"] != "complete": raise WorkflowError("RUN_NOT_COMPLETE")
        workflow.store.audit("workflow_mcp_summary", {"run_id": run_id, "summary": summary, "actor": "mcp-service"})
        return {"status": "recorded", "business_rules_published": False}

    @mcp.tool()
    def govkb_check_registered_sources() -> dict:
        """Trigger the existing synthetic worker, with single-run fencing."""
        return workflow.heartbeat(force=True)

    async def ticker():
        while True:
            try: await asyncio.to_thread(workflow.heartbeat)
            except Exception as error:
                workflow.store.audit("workflow_heartbeat_error", {"code": type(error).__name__})
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            task = asyncio.create_task(ticker()) if start_worker else None
            yield
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

    def session(request, professional=False):
        value = sessions.get(request.cookies.get("gov_workflow_session"))
        if not value or value["expires"] < time.time(): raise PermissionError("LOGIN_REQUIRED")
        if professional and value["role"] != "professional": raise PermissionError("PROFESSIONAL_REQUIRED")
        return value

    async def home(request):
        return HTMLResponse((Path(__file__).with_name("workbench.html")).read_text("utf-8"), headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    async def login(request):
        value = await request.json()
        if not isinstance(value, dict): raise WorkflowError("OBJECT_BODY_REQUIRED")
        role = value.get("role")
        if role not in {"citizen", "professional"}: raise WorkflowError("INVALID_ROLE")
        supplied = value.get("token", "")
        if not isinstance(supplied, str) or (role == "professional" and not secrets.compare_digest(supplied, professional_token)):
            raise PermissionError("INVALID_PROFESSIONAL_TOKEN")
        # Separate admission budgets: anonymous users cannot consume professional capacity.
        now = time.time()
        for key in list(sessions):
            if sessions[key]["expires"] < now: sessions.pop(key, None)
        key = request.cookies.get("gov_workflow_session")
        current = sessions.get(key)
        if not current or current["role"] != role:
            peers = [k for k, v in sessions.items() if v["role"] == role]
            limit = 1024 if role == "citizen" else 64
            if len(peers) >= limit:
                if role == "professional": raise WorkflowError("SESSION_LIMIT")
                sessions.pop(min(peers, key=lambda k: sessions[k]["expires"]), None)
            key = secrets.token_urlsafe(32)
            # Fixed local engineering identity, never claim government authentication.
            sessions[key] = {"role": role, "actor": "demo-professional" if role == "professional" else "demo-citizen", "queries": set(),
                             "requests": {}, "query_lock": asyncio.Lock()}
        sessions[key]["expires"] = now+14400
        response = JSONResponse({"role": role, "mode": "synthetic_engineering_only"})
        response.set_cookie("gov_workflow_session", key, httponly=True, samesite="strict", max_age=14400)
        return response

    async def logout(request):
        sessions.pop(request.cookies.get("gov_workflow_session"), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("gov_workflow_session")
        return response

    async def api(request):
        action = request.path_params["action"]
        professional = action in {"status", "configure", "profile", "heartbeat", "control", "deadline", "review", "publish", "rollback", "audit", "connections", "probe_connections"}
        user = session(request, professional)
        value = await request.json() if request.method == "POST" else {}
        if not isinstance(value, dict): raise WorkflowError("OBJECT_BODY_REQUIRED")
        if action == "me": return JSONResponse({"role": user["role"], "mode": "synthetic_engineering_only"})
        if action == "bootstrap":
            snap = workflow.version()["snapshot"]
            return JSONResponse({"matter": snap["matter"], "fields": snap["fields"], "demo_date": snap["demo_date"], "versions": snap["versions"]})
        if action == "branches": return JSONResponse(workflow.branches(value.get("facts", {}), value.get("parent")))
        if action == "query":
            parent = value.get("parent_task")
            if parent and parent not in user["queries"] and user["role"] != "professional": raise PermissionError("QUERY_NOT_OWNED")
            request_id = value.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id)):
                raise WorkflowError("INVALID_REQUEST_ID")
            signature = canonical_hash({k: v for k, v in value.items() if k != "request_id"})
            async with user["query_lock"]:
                previous = user["requests"].get(request_id) if request_id else None
                if previous:
                    if previous["signature"] != signature: raise WorkflowError("REQUEST_ID_REUSED_WITH_OTHER_INPUT")
                    history = await asyncio.to_thread(workflow.history, previous["task_id"])
                    # Replaying the ID must still apply newly discovered suspensions.
                    answer = {**history["original_answer"], **history["current_availability"], "idempotent_replay": True}
                else:
                    if len(user["queries"]) >= 200: raise WorkflowError("QUERY_SESSION_LIMIT")
                    answer = await asyncio.to_thread(workflow.query, value.get("facts", {}), value["branch_id"], parent_task=parent,
                                                    question=value.get("question", ""))
                    user["queries"].add(answer["task_id"])
                    if request_id: user["requests"][request_id] = {"signature": signature, "task_id": answer["task_id"]}
                return JSONResponse(answer)
        if action == "history":
            key = request.query_params.get("task_id")
            if key not in user["queries"] and user["role"] != "professional": raise PermissionError("QUERY_NOT_OWNED")
            return JSONResponse(workflow.history(key))
        if action == "search": return JSONResponse(workflow.search_documents(value.get("text", "")))
        if action == "source":
            # No paths/URLs supplied by the browser can be fetched or opened.
            if request.query_params.get("documents_version"):
                docs = workflow.documents(request.query_params["documents_version"])
                assets = docs["assets"]
            else:
                version = workflow.version(request.query_params.get("version"))
                assets = version["snapshot"]["assets"]
            asset = next((a for a in assets if a["id"] == request.query_params.get("id")), None)
            if not asset: raise WorkflowError("ASSET_NOT_FOUND")
            return JSONResponse(asset)
        if action == "status": return JSONResponse(workflow.status(int(request.query_params.get("page", "0"))))
        def connection_state():
            value = workflow.connections.describe()
            path = workflow.store.root / "native-drafts.json"
            native = json.loads(path.read_text("utf-8")) if path.exists() else {}
            value["nexent_drafts"] = {key: native.get(key) for key in ("agents", "skill_id", "published", "model_bindings", "mcp_service")}
            value["last_probe"] = workflow.store.get("state", "wf_connection_probe")
            return value
        if action == "connections": return JSONResponse(connection_state())
        if action == "probe_connections":
            if request.method != "POST": raise WorkflowError("POST_REQUIRED")
            value = await asyncio.to_thread(workflow.connections.probe)
            value["checked_at"] = time.time()
            workflow.store.put("state", "wf_connection_probe", value)
            return JSONResponse(connection_state())
        if action == "audit":
            with workflow.store.db() as db:
                rows = [{"time": r[0], "operation": r[1], "payload": json.loads(r[2])} for r in db.execute(
                    "SELECT time,operation,payload FROM audit ORDER BY id DESC LIMIT 200")]
            return JSONResponse(rows)
        if request.method != "POST": raise WorkflowError("POST_REQUIRED")
        actor = user["actor"]
        if action == "configure": result = workflow.configure_demo(actor=actor)
        elif action == "profile": result = workflow.set_profile(value["profile"], actor=actor)
        elif action == "heartbeat": result = await asyncio.to_thread(workflow.heartbeat, force=True)
        elif action == "control": result = workflow.control(paused=value["paused"], interval=value["interval"], actor=actor)
        elif action == "deadline": result = workflow.deadline(value["package_id"], value.get("deadline"), actor=actor)
        elif action == "review": result = workflow.review(value["package_id"], value["object_ids"], actor=actor, decision=value["decision"], note=value["note"])
        elif action == "publish": result = workflow.publish(value["package_id"], value["object_ids"], value["expected_head"], actor=actor)
        elif action == "rollback": result = workflow.rollback(value["target"], value["expected_head"], actor=actor)
        else: raise WorkflowError("UNKNOWN_OPERATION")
        return JSONResponse(result or {"ok": True})

    class Gate:
        """Bound actual receive bytes BEFORE framework JSON/body buffering or tool work."""
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http": return await self.app(scope, receive, send)
            request = Request(scope)
            started = False
            async def guarded_send(message):
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                await send(message)
            try:
                is_mcp = request.url.path == "/mcp" or request.url.path.startswith("/mcp/")
                if is_mcp:
                    header = request.headers.get("authorization", "")
                    if not secrets.compare_digest(header, "Bearer "+mcp_token): raise PermissionError("SERVICE_TOKEN_REQUIRED")
                elif request.method == "POST":
                    if request.headers.get("origin") != str(request.base_url).rstrip("/"): raise PermissionError("SAME_ORIGIN_REQUIRED")
                bounded_receive = receive
                if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                    limit = 1024*1024 if is_mcp else 65536
                    length = request.headers.get("content-length")
                    if length is not None and (not length.isascii() or not length.isdigit()):
                        return await JSONResponse({"error": "INVALID_CONTENT_LENGTH"}, status_code=400)(scope, receive, guarded_send)
                    if length is not None and int(length) > limit:
                        return await JSONResponse({"error": "REQUEST_TOO_LARGE"}, status_code=413)(scope, receive, guarded_send)
                    chunks, size = [], 0
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect": return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > limit:
                            return await JSONResponse({"error": "REQUEST_TOO_LARGE"}, status_code=413)(scope, receive, guarded_send)
                        chunks.append(chunk)
                        if not message.get("more_body", False): break
                    body, delivered = b"".join(chunks), False
                    async def bounded_receive():
                        nonlocal delivered
                        if delivered: return await receive()
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                await self.app(scope, bounded_receive, guarded_send)
            except PermissionError as error:
                if started: raise
                await JSONResponse({"error": str(error)}, status_code=403)(scope, receive, guarded_send)
            except (WorkflowError, DecisionError, KBError, ValueError, KeyError, TypeError):
                if started: raise
                # No raw provider exception bodies, paths or credentials reach the UI.
                await JSONResponse({"error": "操作未通过校验；请检查选择、权限和当前版本。"}, status_code=409)(scope, receive, guarded_send)

    app = Starlette(routes=[Route("/", home), Route("/login", login, methods=["POST"]), Route("/logout", logout, methods=["POST"]),
        Route("/api/{action}", api, methods=["GET", "POST"]), Mount("/mcp", app=mcp.streamable_http_app())], lifespan=lifespan)
    app.add_middleware(Gate)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "host.docker.internal"])
    app.state.workflow_mcp = mcp
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime/agent-workflow")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    # Use the repository's existing private-runtime ACL implementation.
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from private_runtime import ensure_private_directory
    ensure_private_directory(args.runtime)
    path = args.runtime / "access.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"professional_token": secrets.token_urlsafe(36), "mcp_token": secrets.token_urlsafe(36)}, stream)
    credentials = json.loads(path.read_text("utf-8"))
    uvicorn.run(create_app(Workflow(args.runtime), **credentials), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__": main()

"""Loopback M2 UI with real Nexent login, native agents and actual MCP calls."""

import argparse
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from gov_mcp.core import result


def create_app(runtime: Path, config_url="http://127.0.0.1:5010", mcp_url="http://127.0.0.1:8770/sse", native_url="http://127.0.0.1:5018"):
    runtime = Path(runtime)
    sessions = {}
    token = (runtime / "mcp-token.txt").read_text(encoding="ascii").strip()
    security = TransportSecurityMiddleware(TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    def record(user, action, value, **extra):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "action": action,
            "principal_sha256": hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest(),
            "result": value, **extra}
        with (runtime / "workbench-audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def identity(request):
        session = sessions.get(request.cookies.get("govm2_session"))
        if not session or session["expires"] <= time.monotonic():
            return None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.get(config_url + "/user/current_user_info", headers={"Authorization": "Bearer " + session["token"]})
                response.raise_for_status()
                user = response.json()["data"]["user"]
            if not user.get("tenant_id") or not user.get("user_id"):
                return None
            return user, session
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def role_for(user):
        try:
            value = json.loads((runtime / "mcp/role-map.json").read_text(encoding="utf-8"))
            role = value.get(user["tenant_id"], {}).get(user["user_id"], {}).get("role")
            return role if role in ("citizen", "staff") else None
        except (OSError, ValueError, AttributeError):
            return None

    async def call(name, user, **arguments):
        def factory(**kwargs):
            return httpx.AsyncClient(trust_env=False, **kwargs)
        try:
            async with sse_client(mcp_url, headers={"Authorization": "Bearer " + token}, timeout=5,
                                  sse_read_timeout=15, httpx_client_factory=factory) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    response = await client.call_tool("govm2_" + name,
                        {"tenant_id": user["tenant_id"], "user_id": user["user_id"], **arguments})
                    value = result("ERROR", "TOOL_EXECUTION_FAILED") if response.isError else (response.structuredContent or json.loads(response.content[0].text))
        except Exception:
            value = result("ERROR", "CONNECTION_OR_TOOL_UNAVAILABLE", attempts=1, role=role_for(user))
        record(user, "govm2_" + name, value)
        return value

    async def home(request):
        return HTMLResponse(Path(__file__).with_name("m2_workbench.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def login(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"email", "password"} or not all(isinstance(v, str) and v for v in body.values()):
                return JSONResponse(result("BLOCKED", "INVALID_LOGIN"), status_code=400)
            async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
                response = await client.post(config_url + "/user/signin", json=body)
                response.raise_for_status()
                access_token = response.json()["data"]["session"]["access_token"]
            key = secrets.token_urlsafe(32)
            sessions[key] = {"token": access_token, "expires": time.monotonic()+1800}
            value = JSONResponse({"status": "OK"})
            value.set_cookie("govm2_session", key, httponly=True, samesite="strict", max_age=1800)
            value.headers["Cache-Control"] = "no-store"
            return value
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return JSONResponse(result("BLOCKED", "LOGIN_FAILED"), status_code=401)

    async def logout(request):
        sessions.pop(request.cookies.get("govm2_session"), None)
        value = JSONResponse({"status": "OK"})
        value.delete_cookie("govm2_session")
        return value

    async def state(request):
        actor = await identity(request)
        if actor is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        return JSONResponse(await call("get_environment_status", actor[0]), headers={"Cache-Control": "no-store"})

    async def operation(request):
        actor = await identity(request)
        if actor is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        allowed = {"create_task": {"question"}, "search_evidence": {"task_id", "query"},
            "declare_materials": {"task_id", "materials"}, "create_handoff": {"task_id", "idempotency_key"},
            "list_handoffs": set(), "check_case_prerequisites": {"handoff_id"},
            "confirm_review": {"handoff_id", "assessment_sha256", "confirmed"}}
        name = request.path_params["name"]
        try:
            body = await request.json()
            if name not in allowed or not isinstance(body, dict) or set(body)-allowed[name]:
                raise ValueError()
        except (ValueError, TypeError):
            return JSONResponse(result("BLOCKED", "INVALID_ARGUMENTS"), status_code=400)
        return JSONResponse(await call(name, actor[0], **body))

    async def native(request):
        actor = await identity(request)
        if actor is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        user, session = actor
        role = role_for(user)
        conversation = None
        if role is None:
            return JSONResponse(result("BLOCKED", "ROLE_NOT_CONFIGURED"))
        try:
            if await request.json() != {}:
                return JSONResponse(result("BLOCKED", "INVALID_ARGUMENTS"), status_code=400)
            state = json.loads((runtime / "nexent-state.json").read_text(encoding="utf-8"))
            headers = {"Authorization": "Bearer " + session["token"]}
            events = []
            deadline = time.monotonic() + 240
            async with httpx.AsyncClient(trust_env=False, timeout=180) as client:
                created = await client.put(native_url + "/conversation/create", json={"title": "M2-preview-" + role}, headers=headers)
                created.raise_for_status()
                conversation = created.json()["data"]["conversation_id"]
                payload = {"query": "请按实际身份运行当前M2工程演示任务，保留工具的引用、版本、待复核提示与实际完整结果。",
                    "conversation_id": conversation, "agent_id": state["agents"]["govm2_coordinator"],
                    "model_id": state["models"][state["active_model"]], "version_no": state["versions"]["govm2_coordinator"],
                    "is_debug": False, "history": [], "requested_output_tokens": 1024,
                    "enable_plan": False, "enable_automation_tool": False}
                async with client.stream("POST", native_url + "/agent/run", json=payload, headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if time.monotonic() > deadline:
                            raise TimeoutError("Native preview deadline")
                        if line.startswith("data:") and line[5:].strip():
                            events.append(json.loads(line[5:].strip()))
            finals = [e["content"] for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
            value = finals[0] if len(finals)==1 and isinstance(finals[0], dict) else json.loads("".join(finals))
            if not isinstance(value, dict) or value.get("data", {}).get("role") != role or not all(k in value for k in ("status", "citations", "knowledge_version", "message_code")):
                raise ValueError()
            traces = runtime / "native-traces"
            traces.mkdir(exist_ok=True)
            (traces / f"{conversation}.json").write_text(json.dumps({"conversation_id": conversation, "role": role,
                "events": events, "result": value}, ensure_ascii=False, indent=2), encoding="utf-8")
            record(user, "native_agent", value, conversation_id=conversation, role=role)
            return JSONResponse({"result": value, "conversation_id": conversation})
        except (OSError, httpx.HTTPError, ValueError, KeyError, TypeError):
            if conversation is not None:
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=10) as stop_client:
                        await stop_client.get(native_url + f"/agent/stop/{conversation}",
                            headers={"Authorization": "Bearer " + session["token"]})
                except httpx.HTTPError:
                    pass
            value = result("ERROR", "NATIVE_AGENT_UNAVAILABLE_OR_RESULT_INVALID", role=role)
            record(user, "native_agent_error", value)
            return JSONResponse({"result": value}, status_code=502)

    app = Starlette(routes=[Route("/", home), Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]), Route("/api/state", state),
        Route("/api/tools/{name}", operation, methods=["POST"]), Route("/api/agent", native, methods=["POST"])])

    async def gated(scope, receive, send):
        if scope["type"] == "http":
            from starlette.requests import Request
            request = Request(scope)
            rejected = await security.validate_request(request, is_post=scope["method"]=="POST")
            if rejected is not None:
                code = "ORIGIN_REJECTED" if rejected.status_code == 403 else "HOST_REJECTED"
                return await JSONResponse(result("BLOCKED", code), status_code=rejected.status_code)(scope, receive, send)
            if scope["method"]=="POST" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
                return await JSONResponse(result("BLOCKED", "ORIGIN_REJECTED"), status_code=403)(scope, receive, send)
        await app(scope, receive, send)
    return gated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

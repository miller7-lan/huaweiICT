"""Loopback two-role consultation UI. Public request bodies never choose identity."""
import argparse
import json
from pathlib import Path
import secrets
import socket
import time

from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
import uvicorn

from gov_mcp.knowledge.store import KBError
from gov_mcp.matter_auth import LocalEngineeringAuth, NexentAuth
from gov_mcp.matter_graph import MatterProofError
from gov_mcp.matter_guidance import TOPICS
from gov_mcp.matter_lifecycle import MatterLifecycle, LifecycleError

COOKIE = "govmatter_session"
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
ROLE_LABELS = {"citizen": "群众咨询", "staff": "工作人员", "maintainer": "维护者", "professional": "业务复核"}

def create_app(service, authenticator, *, port=8783, session_clock=time.monotonic):
    sessions, login_attempts = {}, {}
    security = TransportSecurityMiddleware(TransportSecuritySettings(
        allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
        allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"]))

    def json_response(value, status=200):
        return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": CSP})

    def denied(message="操作未完成，请核对条件或联系维护者。", code="BLOCKED", status=409):
        return json_response({"status": "BLOCKED", "code": code, "message": message,
                              "knowledge_published": False}, status)

    async def body(request, required, optional=()):
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > 4096:
                raise ValueError("request too large")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
            raise ValueError("invalid fields")
        return value

    async def identity(request):
        key = request.cookies.get(COOKIE)
        session = sessions.get(key)
        if not session or session["expires"] <= session_clock():
            sessions.pop(key, None)
            return None, None
        try:
            principal = await authenticator.resolve(session["credential"])
        except Exception:
            sessions.pop(key, None)
            return None, None
        return principal, session

    async def static(request):
        name = request.path_params.get("name", "matter_web.html")
        allowed = {"matter_web.html": "text/html", "matter_web.js": "text/javascript", "matter_web.css": "text/css"}
        if name not in allowed:
            return Response(status_code=404)
        return Response(Path(__file__).with_name(name).read_bytes(), media_type=allowed[name])

    async def favicon(request):
        return Response(status_code=204)

    async def session_state(request):
        principal, session = await identity(request)
        if principal is None:
            return json_response({"logged_in": False, "authentication_mode": authenticator.mode})
        return json_response({"logged_in": True, "role": principal.role,
            "role_label": ROLE_LABELS[principal.role], "csrf": session["csrf"],
            "authentication_mode": authenticator.mode})

    async def login(request):
        try:
            data = await body(request, {"username", "password"})
            if any(not isinstance(v, str) or not v or len(v) > 256 for v in data.values()):
                raise ValueError()
            key = (request.client.host, data["username"])
            now = session_clock()
            attempts = [t for t in login_attempts.get(key, []) if t > now - 60]
            if len(attempts) >= 8:
                return denied("尝试次数过多，请稍后再试。", "LOGIN_RATE_LIMIT", 429)
            login_attempts[key] = attempts + [now]
            credential = await authenticator.login(data["username"], data["password"])
            principal = await authenticator.resolve(credential)
            sessions.pop(request.cookies.get(COOKIE), None)
            token = secrets.token_urlsafe(32)
            sessions[token] = {"credential": credential, "csrf": secrets.token_urlsafe(32),
                               "expires": now + 1800}
            response = json_response({"status": "OK", "role_label": ROLE_LABELS[principal.role]})
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=1800)
            return response
        except Exception:
            return denied("登录失败，请核对账户或身份服务状态。", "LOGIN_FAILED", 401)

    async def state(request):
        principal, session = await identity(request)
        if principal is None:
            return denied("请先登录。", "LOGIN_REQUIRED", 401)
        value = {"version": service.head(), "role": principal.role,
            "topics": {k: v["label"] for k, v in TOPICS.items()},
            "authentication_mode": authenticator.mode,
            "knowledge_published": False, "formal_business_review": "pending", "cases": []}
        if principal.role in ("citizen", "staff"):
            value["cases"] = [{"case_id": c["id"], "topic": c.get("topic", "old_card_lifecycle"),
                "version": c["version"], "state": c["state"]} for c in service.store.rows("runs")
                if principal.id in (c["owner"], c["assignee"])]
        if principal.role == "maintainer":
            value["sources"] = [{"source_id": source_id, **service.store.get("sources", source_id)}
                for source_id in service.store.get("state", "matter_source_set") or []]
            value["versions"] = [{"id": v["id"], "ontology_stage": v["data"]["candidate"]["ontology_stage"],
                                  "created_at": v["created_at"]} for v in service.store.rows("versions")]
        return json_response(value)

    async def operation(request):
        principal, session = await identity(request)
        if principal is None:
            return denied("登录已失效，请重新登录。", "LOGIN_REQUIRED", 401)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            return denied("会话校验未通过，请刷新页面。", "CSRF_REJECTED", 403)
        name = request.path_params["name"]
        fields = {"query": ({"facts", "topic"}, set()), "read": ({"case_id"}, set()),
            "handoff": ({"case_id"}, set()), "accept": ({"case_id", "token"}, set()),
            "confirm": ({"case_id", "acknowledge", "note"}, set()),
            "source": ({"source_id", "enabled", "reason"}, set()),
            "rollback": ({"target", "expected_head"}, set()), "logout": (set(), set())}
        if name not in fields:
            return denied("没有该操作。", "UNKNOWN_OPERATION", 404)
        try:
            data = await body(request, *fields[name])
            if any(k.endswith("_id") and (not isinstance(v, str) or len(v) > 100) for k, v in data.items()):
                raise ValueError()
            def execute():
                if name == "query":
                    if not isinstance(data["facts"], dict) or not isinstance(data["topic"], str):
                        raise ValueError()
                    if principal.role in ("citizen", "staff"):
                        return service.open_case(data["facts"], topic=data["topic"], principal=principal)
                    return service.query(data["facts"], topic=data["topic"], principal=principal)
                if name == "read":
                    return service.read_case(data["case_id"], principal=principal)
                if name == "handoff":
                    return service.handoff(data["case_id"], principal=principal)
                if name == "accept":
                    return service.accept(data["case_id"], data["token"], principal=principal)
                if name == "confirm":
                    return service.confirm_handoff(data["case_id"], acknowledge=data["acknowledge"],
                                                   note=data["note"], principal=principal)
                if name == "source":
                    service.set_source_enabled(data["source_id"], data["enabled"],
                                               reason=data["reason"], principal=principal)
                    return {"status": "OK", "knowledge_published": False}
                if name == "rollback":
                    return service.rollback(data["target"], data["expected_head"], principal=principal)
                return {"status": "OK"}
            result = await run_in_threadpool(execute)
            response = json_response(result)
            if name == "logout":
                sessions.pop(request.cookies.get(COOKIE), None)
                response.delete_cookie(COOKIE)
            return response
        except (LifecycleError, MatterProofError, KBError) as error:
            code = str(error).split(":")[0]
            messages = {"SOURCE_PAUSED": "相关来源已暂停，当前与历史答复均停止。",
                "SOURCE_CHANGED": "来源有变化，旧答复已暂停，请等待重新复核。",
                "SOURCE_STALE": "来源检查已过期，请等待维护者完成实际复查。",
                "BASE_VERSION_CHANGED": "版本已更新，请刷新后重新确认。"}
            if code in messages:
                return denied(messages[code], code)
            return denied(str(error) if isinstance(error, (LifecycleError, MatterProofError)) else "版本或原件校验未通过。",
                          "OPERATION_REJECTED", 403 if "权限" in str(error) or "无权" in str(error) else 409)
        except (ValueError, TypeError, KeyError):
            return denied("参数或条件无效，请重新选择。", "INVALID_ARGUMENTS", 400)
        except Exception:
            return denied("服务或原件暂不可用，已停止答复。", "SERVICE_UNAVAILABLE", 503)

    app = Starlette(routes=[
        Route("/", static), Route("/favicon.ico", favicon), Route("/assets/{name}", static),
        Route("/api/session", session_state), Route("/api/login", login, methods=["POST"]),
        Route("/api/state", state), Route("/api/{name}", operation, methods=["POST"])])

    async def gated(scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            rejected = await security.validate_request(request, is_post=scope["method"] == "POST")
            if rejected is not None:
                return await denied("页面来源校验未通过。", "HOST_OR_ORIGIN_REJECTED", rejected.status_code)(scope, receive, send)
            if scope["method"] == "POST" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
                return await denied("跨站请求已拒绝。", "ORIGIN_REJECTED", 403)(scope, receive, send)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 4096:
                return await denied("请求内容过大。", "REQUEST_TOO_LARGE", 413)(scope, receive, send)
        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer", "Content-Security-Policy": CSP}.items():
                    headers[key] = value
            await send(message)
        await app(scope, receive, secure_send)
    return gated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("端口已占用，保留现有服务")
    settings = json.loads((args.runtime / "web-settings.json").read_text(encoding="utf-8"))
    if settings.get("auth_mode") not in ("local_engineering_accounts", "nexent_accounts"):
        parser.error("未配置有效的认证方式，不自动切换身份服务")
    auth = LocalEngineeringAuth(args.runtime / "local-accounts.json") if settings["auth_mode"] == "local_engineering_accounts" else NexentAuth(
        args.runtime / "nexent-roles.json", settings.get("nexent_url", "http://127.0.0.1:5010"))
    uvicorn.run(create_app(MatterLifecycle(args.runtime / "service"), auth, port=args.port),
                host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()

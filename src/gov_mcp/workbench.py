"""Loopback M1 workbench using verified Nexent sessions and the actual govx MCP."""

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


def create_app(runtime: Path, config_url="http://127.0.0.1:5010", mcp_url="http://127.0.0.1:8766/sse"):
    sessions = {}
    token = (runtime / "mcp-token.txt").read_text(encoding="ascii").strip()
    security = TransportSecurityMiddleware(TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    async def identity(request):
        entry = sessions.get(request.cookies.get("govx_session"))
        if not entry or entry["expires"] <= time.monotonic():
            return None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.get(config_url + "/user/current_user_info",
                    headers={"Authorization": "Bearer " + entry["token"]})
                response.raise_for_status()
                user = response.json()["data"]["user"]
                if not user.get("tenant_id") or not user.get("user_id"):
                    return None
                return user
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    async def call(name, user, **arguments):
        def mapped_role():
            roles = runtime / "mcp/role-map.json"
            if not roles.exists():
                roles = runtime / "role-map.json"
            try:
                principal = json.loads(roles.read_text(encoding="utf-8")).get(user["tenant_id"], {}).get(user["user_id"], {})
                role = principal.get("role")
                return role if role in ("citizen", "staff") else None
            except (OSError, ValueError, TypeError, AttributeError):
                return None
        def local_client(**kwargs):
            return httpx.AsyncClient(trust_env=False, **kwargs)
        try:
            # One connection attempt; tool's actual bounded storage retry is
            # distinct from a connection failure. No exception means EMPTY.
            async with sse_client(mcp_url, headers={"Authorization": "Bearer " + token},
                                  timeout=5, sse_read_timeout=12, httpx_client_factory=local_client) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    response = await client.call_tool("govx_" + name,
                        {"tenant_id": user["tenant_id"], "user_id": user["user_id"], **arguments})
                    if response.isError:
                        value = result("ERROR", "TOOL_EXECUTION_FAILED")
                    else:
                        value = response.structuredContent or json.loads(response.content[0].text)
        except Exception:
            value = result("ERROR", "CONNECTION_OR_TOOL_UNAVAILABLE", attempts=1, role=mapped_role(),
                           role_basis="controlled_mapping_from_authenticated_identity")
        # Never log request bodies, cookies, tokens or passwords.
        entry = {"time": datetime.now(timezone.utc).isoformat(), "tool": "govx_" + name,
                 "principal_sha256": hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest(),
                 "role": value.get("data", {}).get("role"), "result": value}
        with (runtime / "workbench-audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return value

    async def home(request):
        return HTMLResponse(Path(__file__).with_name("workbench.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def login(request):
        try:
            body = await request.json()
            if set(body) != {"email", "password"} or not all(isinstance(v, str) and v for v in body.values()):
                return JSONResponse(result("BLOCKED", "INVALID_LOGIN"), status_code=400)
            async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
                response = await client.post(config_url + "/user/signin", json=body)
                response.raise_for_status()
                access_token = response.json()["data"]["session"]["access_token"]
            key = secrets.token_urlsafe(32)
            sessions[key] = {"token": access_token, "expires": time.monotonic() + 1800}
            response = JSONResponse({"status": "OK"})
            response.set_cookie("govx_session", key, httponly=True, samesite="strict", max_age=1800)
            response.headers["Cache-Control"] = "no-store"
            return response
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return JSONResponse(result("BLOCKED", "LOGIN_FAILED"), status_code=401)

    async def logout(request):
        sessions.pop(request.cookies.get("govx_session"), None)
        response = JSONResponse({"status": "OK"})
        response.delete_cookie("govx_session")
        return response

    async def state(request):
        user = await identity(request)
        if user is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        return JSONResponse(await call("get_environment_status", user), headers={"Cache-Control": "no-store"})

    async def workbench(request):
        user = await identity(request)
        if user is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        return JSONResponse(await call("check_case_prerequisites", user), headers={"Cache-Control": "no-store"})

    async def operation(request):
        user = await identity(request)
        if user is None:
            return JSONResponse(result("BLOCKED", "LOGIN_REQUIRED"), status_code=401)
        name = request.path_params["name"]
        allowed = {"query_knowledge_graph": set(), "prepare_update": {"inputs"},
                   "publish_update": {"candidate_id", "confirmed"}}
        body = await request.json()
        if name not in allowed or not isinstance(body, dict) or set(body) - allowed[name]:
            return JSONResponse(result("BLOCKED", "INVALID_ARGUMENTS"), status_code=400)
        return JSONResponse(await call(name, user, **body))

    app = Starlette(routes=[Route("/", home), Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]), Route("/api/state", state),
        Route("/api/workbench", workbench), Route("/api/tools/{name}", operation, methods=["POST"])])

    async def gated(scope, receive, send):
        if scope["type"] == "http":
            from starlette.requests import Request
            request = Request(scope)
            rejected = await security.validate_request(request, is_post=scope["method"] == "POST")
            if rejected is not None:
                return await rejected(scope, receive, send)
            if scope["method"] == "POST":
                # Require same-origin POSTs, including login/logout.
                if request.headers.get("origin") != str(request.base_url).rstrip("/"):
                    return await JSONResponse(result("BLOCKED", "ORIGIN_REJECTED"), status_code=403)(scope, receive, send)
        await app(scope, receive, send)
    return gated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

"""Serve three authenticated MCP tools over SSE."""

import argparse
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gov_mcp.core import BlankStore, GovernmentTools


class BearerGate:
    def __init__(self, app, token: str, security=None):
        self.app = app
        self.token = token
        self.security = security

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"")
            if not secrets.compare_digest(supplied, f"Bearer {self.token}".encode("ascii")):
                await JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)(scope, receive, send)
                return
            if self.security:
                rejected = await self.security.validate_request(Request(scope), is_post=scope["method"] == "POST")
                if rejected is not None:
                    await rejected(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(runtime: Path, token: str):
    if len(token) < 32 or not token.isascii():
        raise ValueError("A dedicated MCP bearer token of at least 32 characters is required")
    runtime.mkdir(parents=True, exist_ok=True)
    roles = runtime / "role-map.json"
    if not roles.exists():
        roles.write_text("{}\n", encoding="utf-8")
    tools = GovernmentTools(BlankStore(runtime / "business.sqlite3"), roles)
    mcp = FastMCP("gov_blank_mcp", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "gov-blank-mcp:8765"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    def record(name: str, operation, tenant_id: str, user_id: str):
        started = time.perf_counter()
        value = operation(tenant_id, user_id)
        value["data"]["request_id"] = secrets.token_hex(8)
        entry = {
            "time": datetime.now(timezone.utc).isoformat(), "tool": name,
            "request_id": value["data"]["request_id"],
            "status": value["status"], "message_code": value["message_code"],
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "principal_sha256": hashlib.sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest(),
            "result": value,
        }
        with (runtime / "tool-audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
        return value

    @mcp.tool()
    def get_environment_status(tenant_id: str, user_id: str) -> dict:
        """Get environment state and authenticated business role. Identity is injected by Nexent."""
        return record("get_environment_status", tools.get_environment_status, tenant_id, user_id)

    @mcp.tool()
    def search_evidence(tenant_id: str, user_id: str) -> dict:
        """Inspect empty evidence storage. EMPTY is terminal; never retry it or invent evidence."""
        return record("search_evidence", tools.search_evidence, tenant_id, user_id)

    @mcp.tool()
    def check_case_prerequisites(tenant_id: str, user_id: str) -> dict:
        """Check staff permission and missing prerequisites. BLOCKED is terminal, never an approval verdict."""
        return record("check_case_prerequisites", tools.check_case_prerequisites, tenant_id, user_id)

    async def health(request):
        return JSONResponse({"service": "gov_blank_mcp", "status": "UP"})

    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(mcp.settings.transport_security))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime", type=Path, default=Path(".runtime/mcp"))
    args = parser.parse_args()
    token = os.environ.get("GOV_MCP_TOKEN", "")
    if not token:
        token = (args.runtime / "mcp-token.txt").read_text(encoding="utf-8").strip()
    uvicorn.run(create_app(args.runtime, token), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

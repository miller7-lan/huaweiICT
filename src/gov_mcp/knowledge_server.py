"""Bearer-protected MCP for published evidence, available to the existing Nexent platform."""

import argparse
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from gov_mcp.knowledge_tools import KnowledgeTools
from gov_mcp.server import BearerGate


def create_app(runtime, token, port=8773):
    if len(token) < 32 or not token.isascii():
        raise ValueError("Dedicated knowledge MCP bearer required")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Valid advertised MCP port required")
    tools = KnowledgeTools(runtime)
    mcp = FastMCP("govkb_public_evidence_mcp", transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", f"host.docker.internal:{port}"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    @mcp.tool()
    def govkb_get_environment_status(tenant_id: str, user_id: str) -> dict:
        """Read mapped identity, fixed trial scope and publication readiness. No candidate quotations."""
        return tools.execute("get_environment_status", tenant_id, user_id)

    @mcp.tool()
    def govkb_search_evidence(tenant_id: str, user_id: str, query: str, knowledge_version: str | None = None, as_of: str | None = None) -> dict:
        """Read only reviewed published evidence. Stop on EMPTY/BLOCKED/ERROR; never infer rules from memory.

        Omit knowledge_version for a new consultation. Reuse the returned version for later calls
        in the same consultation/handoff. Candidate sources must never be returned as current rules.
        tenant_id/user_id must be injected by the authenticated platform session, not visitor text.
        """
        return tools.execute("search_evidence", tenant_id, user_id, query=query, knowledge_version=knowledge_version, as_of=as_of)

    async def health(request):
        return JSONResponse({"status": "UP", "service": "govkb_public_evidence_mcp", "read_only": True})

    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(mcp.settings.transport_security))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8773)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime, os.environ["GOVKB_MCP_TOKEN"], args.port), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

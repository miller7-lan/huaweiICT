"""Six govx tools on a separate SSE endpoint and separate private runtime."""

import argparse
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from gov_mcp.extension import ExtensionTools
from gov_mcp.server import BearerGate


def create_app(runtime: Path, token: str):
    if len(token) < 32 or not token.isascii():
        raise ValueError("Dedicated extension bearer required")
    tools = ExtensionTools(runtime)
    mcp = FastMCP("govx_blank_mcp", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "govx-blank-mcp:8766"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    @mcp.tool()
    def govx_get_environment_status(tenant_id: str, user_id: str) -> dict:
        """Read authenticated role and configured resources. Identity is injected by Nexent."""
        return tools.execute("get_environment_status", tenant_id, user_id)

    @mcp.tool()
    def govx_search_evidence(tenant_id: str, user_id: str) -> dict:
        """Empty evidence lookup. EMPTY/BLOCKED/ERROR must terminate the agent."""
        return tools.execute("search_evidence", tenant_id, user_id)

    @mcp.tool()
    def govx_query_knowledge_graph(tenant_id: str, user_id: str) -> dict:
        """Read empty graph, returning no invented nodes or relationships."""
        return tools.execute("query_knowledge_graph", tenant_id, user_id)

    @mcp.tool()
    def govx_check_case_prerequisites(tenant_id: str, user_id: str) -> dict:
        """Staff only. Missing case/material/evidence blocks any approval conclusion."""
        return tools.execute("check_case_prerequisites", tenant_id, user_id)

    @mcp.tool()
    def govx_prepare_update(tenant_id: str, user_id: str, inputs: list[str] | None = None) -> dict:
        """Knowledge maintainer only. No input means NO_CHANGES, not a scan of external policies."""
        return tools.execute("prepare_update", tenant_id, user_id, inputs=[] if inputs is None else inputs)

    @mcp.tool()
    def govx_publish_update(tenant_id: str, user_id: str, candidate_id: str | None = None, confirmed: bool = False) -> dict:
        """Knowledge maintainer and human confirmation required. M1 has no candidate to publish."""
        return tools.execute("publish_update", tenant_id, user_id, candidate_id=candidate_id, confirmed=confirmed)

    async def health(request):
        return JSONResponse({"service": "govx_blank_mcp", "status": "UP", "phase": "M1"})

    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(mcp.settings.transport_security))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime, os.environ["GOVX_MCP_TOKEN"]), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

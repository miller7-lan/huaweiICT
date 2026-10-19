"""Independent, bearer-protected M2 preview MCP."""

import argparse
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from gov_mcp.m2 import M2Tools
from gov_mcp.server import BearerGate


def create_app(runtime: Path, token: str):
    if len(token) < 32 or not token.isascii():
        raise ValueError("Dedicated M2 bearer required")
    tools = M2Tools(runtime)
    mcp = FastMCP("govm2_preview_mcp", transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "govm2-mcp:8770"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ))

    @mcp.tool()
    def govm2_get_environment_status(tenant_id: str, user_id: str) -> dict:
        """Read actual injected identity and the demo-preview knowledge state."""
        return tools.execute("get_environment_status", tenant_id, user_id)

    @mcp.tool()
    def govm2_create_task(tenant_id: str, user_id: str, question: str) -> dict:
        """Citizen only. Create a simulated task and pin its source snapshot."""
        return tools.execute("create_task", tenant_id, user_id, question=question)

    @mcp.tool()
    def govm2_search_evidence(tenant_id: str, user_id: str, task_id: str | None = None, query: str | None = None) -> dict:
        """Retrieve actual classified excerpts for the owned pinned preview task. Do not infer approval."""
        return tools.execute("search_evidence", tenant_id, user_id, task_id=task_id, query=query)

    @mcp.tool()
    def govm2_declare_materials(tenant_id: str, user_id: str, task_id: str, materials: dict[str, str]) -> dict:
        """Citizen only. Save presence/missing/unknown flags, never applicant files or identities."""
        return tools.execute("declare_materials", tenant_id, user_id, task_id=task_id, materials=materials)

    @mcp.tool()
    def govm2_create_handoff(tenant_id: str, user_id: str, task_id: str, idempotency_key: str) -> dict:
        """Citizen only. Freeze a cited, version-pinned handoff with an idempotency key."""
        return tools.execute("create_handoff", tenant_id, user_id, task_id=task_id, idempotency_key=idempotency_key)

    @mcp.tool()
    def govm2_list_handoffs(tenant_id: str, user_id: str) -> dict:
        """Staff only. List the current tenant's preview handoffs."""
        return tools.execute("list_handoffs", tenant_id, user_id)

    @mcp.tool()
    def govm2_check_case_prerequisites(tenant_id: str, user_id: str, handoff_id: str | None = None) -> dict:
        """Staff only. Assess explicit demo declarations and flag unreviewed source rules. No approval verdict."""
        return tools.execute("check_case_prerequisites", tenant_id, user_id, handoff_id=handoff_id)

    @mcp.tool()
    def govm2_confirm_review(tenant_id: str, user_id: str, handoff_id: str, assessment_sha256: str, confirmed: bool = False) -> dict:
        """Staff only. Confirm the exact preview assessment; this is not official business/source review."""
        return tools.execute("confirm_review", tenant_id, user_id, handoff_id=handoff_id,
                             assessment_sha256=assessment_sha256, confirmed=confirmed)

    async def health(request):
        return JSONResponse({"service": "govm2_preview_mcp", "phase": "M2", "mode": "engineering_demo", "status": "UP"})

    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(mcp.settings.transport_security))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime, os.environ["GOVM2_MCP_TOKEN"]), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

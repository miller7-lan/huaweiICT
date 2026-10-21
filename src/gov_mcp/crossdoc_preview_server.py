"""Bearer protected fixed cross-document MCP tools with no model or publish API."""
import argparse
import json
from pathlib import Path
import re
import socket

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from gov_mcp.crossdoc_preview import CrossdocPreview, SERVICE, code_fingerprints
from gov_mcp.server import BearerGate


def create_app(preview, token, port=8783):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("DEDICATED_TOKEN_REQUIRED")
    security = TransportSecuritySettings(allowed_hosts=["localhost:*", "127.0.0.1:*", f"host.docker.internal:{port}"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP(SERVICE, transport_security=security)
    startup = code_fingerprints()

    def execute(operation, *args):
        try:
            if code_fingerprints() != startup:
                raise ValueError("SERVICE_SOURCE_CHANGED_RESTART_REQUIRED")
            return operation(*args)
        except Exception as error:
            code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z_]+", str(error)) else "CROSSDOC_UNAVAILABLE"
            return {"status": "blocked", "error_code": code, "published": False}

    @mcp.tool()
    def govkb_crossdoc_open_query(scenario_id: str) -> dict:
        """Open the fixed fictional nanjing_outside_old_card scenario, only during an active run."""
        return execute(preview.open_query, scenario_id)

    @mcp.tool()
    def govkb_crossdoc_retrieve(task_id: str) -> dict:
        """Read the two exact task-bound archived official documents. No URLs or visitor input."""
        return execute(preview.retrieve, task_id)

    @mcp.tool()
    def govkb_crossdoc_verify(task_id: str) -> dict:
        """Compute and check the cross-document proof, exact citations, units and dependencies. No Jev."""
        return execute(preview.verify, task_id)

    @mcp.tool()
    def govkb_crossdoc_state(task_id: str) -> dict:
        """Read persisted task stage. Do not trust a child's prose as completion."""
        return execute(preview.state, task_id)

    @mcp.tool()
    def govkb_crossdoc_finalize(task_id: str) -> dict:
        """Recompute the bound proof and return the exact stored answer. No publication."""
        return execute(preview.finalize, task_id)

    async def health(request):
        return JSONResponse({"service": SERVICE, "status": "UP", "publication_enabled": False,
                             "startup_source_sha256": startup})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("PORT_OCCUPIED_EXISTING_SERVICE_PRESERVED")
    token = json.loads((args.runtime / "access.json").read_text("utf-8"))["mcp_token"]
    uvicorn.run(create_app(CrossdocPreview(args.runtime), token, args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__": main()

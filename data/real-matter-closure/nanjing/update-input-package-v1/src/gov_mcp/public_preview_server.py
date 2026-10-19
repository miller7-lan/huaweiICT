"""Isolated loopback MCP with five fixed-source preview tools and no publication API."""
import argparse
from pathlib import Path
import re

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from gov_mcp.public_preview import Preview, PreviewError, LiveVerifier, read, source_digest
from gov_mcp.server import BearerGate


def create_app(preview, token, port=8781):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("DEDICATED_TOKEN_REQUIRED")
    security = TransportSecuritySettings(allowed_hosts=["localhost:*", "127.0.0.1:*", f"host.docker.internal:{port}"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP("govkb_public_preview", transport_security=security)
    startup_sources = {name: source_digest(Path(__file__).with_name(name).read_bytes())
                       for name in ("public_preview.py", "public_preview_server.py")}

    def execute(operation, *args):
        try:
            return operation(*args)
        except Exception as error:
            code = str(error) if isinstance(error, PreviewError) and re.fullmatch(r"[A-Z_]+", str(error)) else "PREVIEW_UNAVAILABLE"
            return {"status": "blocked", "error_code": code, "mode": "public_preview", "published": False}

    @mcp.tool()
    def govkb_preview_open_query(case: dict, question: str) -> dict:
        """Controller: open only the fixed public fictional case/question. No visitor data."""
        return execute(preview.open_query, case, question)

    @mcp.tool()
    def govkb_preview_retrieve(task_id: str) -> dict:
        """Retriever child: exact full sections from the task's pinned public originals."""
        return execute(preview.retrieve, task_id)

    @mcp.tool()
    def govkb_preview_verify(task_id: str) -> dict:
        """Verifier child: exact citations, scope, translation and one bounded official Jev call. No publication."""
        return execute(preview.verify, task_id)

    @mcp.tool()
    def govkb_preview_state(task_id: str) -> dict:
        """Controller: read persisted stage. Errors are terminal; never trust a child's prose."""
        return execute(preview.state, task_id)

    @mcp.tool()
    def govkb_preview_finalize(task_id: str) -> dict:
        """Controller: recheck current sources/pauses and return the exact stored partial preview."""
        return execute(preview.finalize, task_id)

    async def health(request):
        return JSONResponse({"service": "govkb_public_preview", "status": "UP", "publication_enabled": False,
                             "startup_source_sha256": startup_sources})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--live", action="store_true", help="Permit bounded Jev calls only inside an authorized active native run")
    args = parser.parse_args()
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("PORT_OCCUPIED: existing service is preserved")
    token = read(args.runtime / "access.json")["mcp_token"]
    uvicorn.run(create_app(Preview(args.runtime, LiveVerifier() if args.live else None), token, args.port),
                host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

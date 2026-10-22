"""Dedicated authenticated MCP surface for the reviewed ontology preview."""
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

from gov_mcp.ontology_crossdoc import ArchivedCrossdocSource, CrossdocOntologyWorkspace
from gov_mcp.ontology_crossdoc_preview import OntologyCrossdocPreview, SERVICE, code_fingerprints
from gov_mcp.server import BearerGate


def create_app(preview, token, port=8785):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("DEDICATED_TOKEN_REQUIRED")
    security = TransportSecuritySettings(
        allowed_hosts=["localhost:*", "127.0.0.1:*", f"host.docker.internal:{port}"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP(SERVICE, transport_security=security)
    startup = code_fingerprints()

    def execute(operation, *args):
        try:
            if code_fingerprints() != startup:
                raise ValueError("SERVICE_SOURCE_CHANGED_RESTART_REQUIRED")
            return operation(*args)
        except Exception as error:
            code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z_]+", str(error)) else "ONTOLOGY_CROSSDOC_UNAVAILABLE"
            return {"status": "blocked", "error_code": code, "published": False}

    @mcp.tool()
    def govkb_ontology_crossdoc_open_query(scenario_id: str) -> dict:
        """Open the one fixed fictional scenario during its reserved campaign."""
        return execute(preview.open_query, scenario_id)

    @mcp.tool()
    def govkb_ontology_crossdoc_retrieve(task_id: str) -> dict:
        """Retrieve both full archived documents bound to the reviewed ontology."""
        return execute(preview.retrieve, task_id)

    @mcp.tool()
    def govkb_ontology_crossdoc_verify(task_id: str) -> dict:
        """Execute the active reviewed ontology's typed three-node proof."""
        return execute(preview.verify, task_id)

    @mcp.tool()
    def govkb_ontology_crossdoc_state(task_id: str) -> dict:
        """Read the persisted stage and recheck source and ontology version."""
        return execute(preview.state, task_id)

    @mcp.tool()
    def govkb_ontology_crossdoc_finalize(task_id: str) -> dict:
        """Reexecute the reviewed plan and return the exact persisted answer."""
        return execute(preview.finalize, task_id)

    async def health(request):
        return JSONResponse({"service": SERVICE, "status": "UP", "publication_enabled": False,
                             "startup_source_sha256": startup})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def open_preview(runtime):
    config = json.loads((runtime / "ontology-binding.json").read_text("utf-8"))
    source = ArchivedCrossdocSource(runtime)
    workspace = CrossdocOntologyWorkspace(Path(config["ontology_root"]), source)
    return OntologyCrossdocPreview(runtime, workspace, config["expected_version"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8785)
    args = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("PORT_OCCUPIED_EXISTING_SERVICE_PRESERVED")
    token = json.loads((args.runtime / "access.json").read_text("utf-8"))["mcp_token"]
    uvicorn.run(create_app(open_preview(args.runtime), token, args.port),
                host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

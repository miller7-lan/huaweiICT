"""Fixed task tools for one archived successor update; no publishing/network API."""
import argparse
import json
import re
from pathlib import Path
import socket

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings, TransportSecurityMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from gov_mcp.server import BearerGate
from gov_mcp.knowledge.store import KBError
from gov_mcp.successor_update import SuccessorEngine, SERVICE, implementation


def state_wire(value):
    """Nexent MCP wrappers return strings; expose a fixed control marker, no JSON import needed."""
    if value.get("status") == "blocked": return json.dumps(value, ensure_ascii=False)
    stage = value.get("stage")
    if (stage not in {"discovered", "extracted", "validated", "finished"}
            or value.get("published") is not False or value.get("can_finish") is not (stage == "validated")):
        raise KBError("INVALID_PERSISTED_STAGE_ENVELOPE")
    return "SUCCESSOR_STAGE:" + stage


def create_app(engine, token, port=8784):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii(): raise ValueError("DEDICATED_TOKEN_REQUIRED")
    security = TransportSecuritySettings(allowed_hosts=["localhost:*", "127.0.0.1:*", f"host.docker.internal:{port}"],
                                         allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP(SERVICE, transport_security=security)
    startup = implementation()

    def execute(method, *args):
        try:
            if startup != implementation(): raise KBError("SERVICE_CODE_CHANGED")
            return method(*args)
        except Exception as error:
            message = str(error)
            code = message if isinstance(error, (KBError, ValueError)) and re.fullmatch(r"[A-Z_]+", message) else "SUCCESSOR_TOOL_BLOCKED"
            engine.store.audit("successor_tool_blocked", {"method": method.__name__, "code": code})
            return {"status": "blocked", "error_code": code, "published": False}

    @mcp.tool()
    def govkb_successor_get(run_id: str, view: str) -> dict:
        """Read exactly one complete first-item document view: old then new, once per child stage."""
        return execute(engine.get_batch, run_id, view)

    @mcp.tool()
    def govkb_successor_submit(run_id: str, difference: dict) -> dict:
        """Extractor submits fresh source-supported difference; full first-item clauses required."""
        return execute(engine.submit, run_id, difference)

    @mcp.tool()
    def govkb_successor_review(run_id: str, review: dict) -> dict:
        """Reviewer submits its independent machine check; never a human approval."""
        return execute(engine.review, run_id, review)

    @mcp.tool()
    def govkb_successor_state(run_id: str) -> str:
        """Read fixed SUCCESSOR_STAGE markers for safe native string-return control gates."""
        return state_wire(execute(engine.state, run_id))

    @mcp.tool()
    def govkb_successor_finish(run_id: str) -> dict:
        """Finish the validated job, returning the pending candidate exactly. Never publish."""
        return execute(engine.finish, run_id)

    async def health(request):
        return JSONResponse({"service": SERVICE, "status": "UP", "publication_enabled": False, "startup_source_sha256": startup})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8784)
    args = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0: raise ValueError("EXISTING_SERVICE_PRESERVED")
    token = json.loads((args.runtime / "access.json").read_text("utf-8"))["mcp_token"]
    uvicorn.run(create_app(SuccessorEngine(args.runtime), token, args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__": main()

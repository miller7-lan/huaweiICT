"""Loopback-only read-only maintenance view. Mutations remain explicit local CLI operations."""

import argparse
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from gov_mcp.knowledge import KnowledgeStore


def create_app(runtime):
    store = KnowledgeStore(runtime, read_only=True)

    async def home(request):
        return HTMLResponse((Path(__file__).parent / "knowledge_workbench.html").read_text(encoding="utf-8"),
            headers={"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'", "X-Content-Type-Options": "nosniff"})

    async def status(request):
        return JSONResponse(store.status())

    async def search(request):
        try:
            query = request.query_params
            return JSONResponse(store.search(query.get("q", ""), query.get("region", "郑州市二七区"),
                query.get("matter", "demo-id-renewal"), query.get("version"), query.get("as_of")))
        except (ValueError, OSError) as error:
            return JSONResponse({"status": "BLOCKED", "message": str(error)}, status_code=400)

    async def impact(request):
        from gov_mcp.knowledge_impact import revision_impact
        try:
            revision = request.query_params.get("revision", "")
            if not revision or len(revision) > 80:
                raise ValueError("Revision required")
            return JSONResponse(revision_impact(store, revision))
        except (ValueError, OSError):
            return JSONResponse({"status": "BLOCKED", "message": "变更分析不可用，请核对修订与原件完整性。"}, status_code=400)

    async def favicon(request):
        return Response(status_code=204)

    app = Starlette(routes=[Route("/", home), Route("/favicon.ico", favicon), Route("/api/status", status), Route("/api/search", search), Route("/api/impact", impact)])

    async def loopback(scope, receive, send):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("ascii", errors="replace").split(":")[0]
        if scope.get("type") == "http" and host not in ("127.0.0.1", "localhost", "testserver"):
            await JSONResponse({"status": "BLOCKED"}, status_code=403)(scope, receive, send)
            return
        await app(scope, receive, send)
    return loopback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

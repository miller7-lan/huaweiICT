"""Authenticated MCP retrieval with a synthetic-only original-source viewer."""

import argparse
import html
import json
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.routing import Route

from gov_mcp.asset_query import AssetQuery
from gov_mcp.server import BearerGate


def create_app(runtime):
    runtime = Path(runtime)
    token = (runtime / "mcp-token.txt").read_text(encoding="ascii").strip()
    if len(token) < 32 or not token.isascii():
        raise ValueError("Dedicated MCP token required")
    store = AssetQuery(runtime)
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "govdocs-mcp:8774"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],)
    mcp = FastMCP("govdocs_asset_mcp", transport_security=security)

    @mcp.tool()
    def govdocs_search_evidence(query: str, tenant_id: str, user_id: str, limit: int = 4) -> dict:
        """Search synthetic text, table and image OCR evidence. Use the user's actual query. Identity is injected by Nexent. Return original citations; EMPTY/BLOCKED/ERROR terminate the action."""
        return store.search(tenant_id, user_id, query, limit)

    def page(body):
        return HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='UTF-8'><title>政务资料查询演练</title><style>body{font:17px/1.7 'Microsoft YaHei',sans-serif;max-width:980px;margin:32px auto;padding:0 20px;color:#18212b}article{padding:16px 0;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;word-break:break-word;background:#f3f5f7;padding:16px}img{max-width:100%;border:1px solid #ccc}a{color:#075bb5}small{color:#59636f}</style><h1>政务资料查询演练</h1><p>当前仅展示人工构造的模拟资料，不是官方办事规则。</p>" + body + "</html>",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def health(request):
        try:
            index, assets, segments, _ = store.load()
            return JSONResponse({"status": "UP", "assets": len(assets), "segments": len(segments), "knowledge_version": index["knowledge_version"]})
        except (OSError, ValueError, KeyError, TypeError):
            return JSONResponse({"status": "ERROR", "message_code": "ASSET_SNAPSHOT_UNAVAILABLE"}, status_code=503)

    async def home(request):
        try:
            index, assets, _, _ = store.load()
            blocks = ["<p>在 Nexent 中选择“政务资料查询演练”，提问后可通过答案链接核对原文。</p><p>资料版本：<code>" + html.escape(index["knowledge_version"]) + "</code></p>"]
            for asset in assets:
                blocks.append("<article><h2>" + html.escape(asset["title"]) + "</h2><p>类型：" + html.escape(asset["modality"]) + " · 已解析 · " + str(len(asset["segments"])) + " 个片段</p><small>资产编号：" + html.escape(asset["asset_id"]) + "<br>来源：本机人工构造模拟数据</small><ul>")
                for row in asset["segments"]:
                    blocks.append("<li><a href='/evidence/" + index["knowledge_version"] + "/" + row["segment_id"] + "'>" + html.escape(row["content"]) + "</a></li>")
                blocks.append("</ul></article>")
            return page("".join(blocks))
        except (OSError, ValueError, KeyError, TypeError):
            return page("<p>资料快照不可用，当前无法查询。请重新生成并检查原件与索引。</p>")

    async def evidence(request):
        try:
            index, assets, segments, _ = store.load()
            if request.path_params["version"] != index["knowledge_version"]:
                return JSONResponse({"message_code": "VERSION_NOT_FOUND"}, status_code=404)
            segment = next((row for row in segments if row["segment_id"] == request.path_params["segment_id"]), None)
            if segment is None:
                return JSONResponse({"message_code": "SEGMENT_NOT_FOUND"}, status_code=404)
            asset = next(row for row in assets if row["asset_id"] == segment["asset_id"])
            body = "<p><a href='/'>返回资料清单</a></p><h2>" + html.escape(asset["title"]) + "</h2><h3>引用片段</h3><pre>" + html.escape(segment["content"]) + "</pre><p>原文定位：" + html.escape(json.dumps(segment["locator"], ensure_ascii=False)) + "</p><p>版本：<code>" + index["knowledge_version"] + "</code></p><p>文件 SHA-256：<code>" + asset["sha256"] + "</code></p><h3>原件</h3>"
            if asset["modality"] == "image":
                body += "<img alt='模拟通知原件' src='/original/" + asset["asset_id"] + "'>"
            else:
                raw = runtime / "snapshot" / asset["snapshot_file"]
                body += "<pre>" + html.escape(raw.read_text(encoding="utf-8-sig")) + "</pre>"
            return page(body)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            return JSONResponse({"message_code": "ASSET_SNAPSHOT_UNAVAILABLE"}, status_code=503)

    async def original(request):
        try:
            _, assets, _, _ = store.load()
            asset = next((row for row in assets if row["asset_id"] == request.path_params["asset_id"] and row["modality"] == "image"), None)
            if asset is None:
                return JSONResponse({"message_code": "ASSET_NOT_FOUND"}, status_code=404)
            return FileResponse(runtime / "snapshot" / asset["snapshot_file"], headers={"X-Content-Type-Options": "nosniff"})
        except (OSError, ValueError, KeyError, TypeError):
            return JSONResponse({"message_code": "ASSET_SNAPSHOT_UNAVAILABLE"}, status_code=503)

    app = mcp.sse_app()
    app.router.routes[:0] = [Route("/health", health), Route("/", home), Route("/evidence/{version}/{segment_id}", evidence), Route("/original/{asset_id}", original)]
    gate = BearerGate(app, token, TransportSecurityMiddleware(security))
    public_security = TransportSecurityMiddleware(TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*", "govdocs-mcp:8774"], allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))

    async def routed(scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and scope["method"] == "GET" and (path in ("/", "/health") or path.startswith(("/evidence/", "/original/"))):
            from starlette.requests import Request
            rejected = await public_security.validate_request(Request(scope), is_post=False)
            if rejected is not None:
                return await rejected(scope, receive, send)
            return await app(scope, receive, send)
        await gate(scope, receive, send)
    return routed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=Path(".runtime/asset-query"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    uvicorn.run(create_app(args.runtime), host=args.host, port=args.port, access_log=False)

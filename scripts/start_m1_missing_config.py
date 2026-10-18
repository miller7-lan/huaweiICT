"""Separate missing-connection instance; never withdraw healthy service settings."""

import json
import shutil
import socket
import threading
from pathlib import Path

import uvicorn

from gov_mcp.extension_server import create_app
from gov_mcp.workbench import create_app as create_workbench
from nexent_api import RUNTIME
from private_runtime import ensure_private_directory


def main():
    target = RUNTIME / "m1/missing-config"
    ensure_private_directory(target)
    for port in (8768, 8769):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    shutil.copyfile(RUNTIME / "m1/mcp/role-map.json", target / "role-map.json")
    shutil.copyfile(RUNTIME / "m1/mcp-token.txt", target / "mcp-token.txt")
    (target / "connections.json").write_text(json.dumps({"evidence": True, "graph": False, "updates": False}))
    token = (target / "mcp-token.txt").read_text(encoding="ascii").strip()
    server = uvicorn.Server(uvicorn.Config(create_app(target, token), host="127.0.0.1", port=8768, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        uvicorn.run(create_workbench(target, mcp_url="http://127.0.0.1:8768/sse"),
                    host="127.0.0.1", port=8769, access_log=False)
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()

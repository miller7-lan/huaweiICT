"""Prepare isolated M1 private storage without editing the original runtime."""

import hashlib
import json
import secrets
from datetime import datetime, timezone

from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory
from gov_mcp.core import BlankStore
from gov_mcp.extension import ExtensionStore


def main():
    target = RUNTIME / "m1"
    ensure_private_directory(target)
    ensure_private_directory(target / "mcp")
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    roles_path = target / "mcp/role-map.json"
    if not roles_path.exists():
        roles = {state["tenant_id"]: {
            state["accounts"][r]["user_id"]: {"role": r, "knowledge_maintainer": False}
            for r in ("citizen", "staff")}}
        # The developer is a separate controlled maintenance principal in M1.
        roles[state["tenant_id"]][state["accounts"]["developer"]["user_id"]] = {
            "role": "staff", "knowledge_maintainer": True}
        roles_path.write_text(json.dumps(roles, indent=2), encoding="utf-8")
    connections = target / "mcp/connections.json"
    if not connections.exists():
        connections.write_text(json.dumps({"evidence": True, "graph": True, "updates": True}), encoding="utf-8")
    token_path = target / "mcp-token.txt"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48), encoding="ascii")
    (target / "mcp.env").write_text("GOVX_MCP_TOKEN=" + token_path.read_text(encoding="ascii").strip() + "\n", encoding="ascii")
    baseline = ROOT / "evidence/m1-baseline.json"
    if not baseline.exists():
        paths = ["src/gov_mcp/core.py", "src/gov_mcp/server.py", "config/agents.nexent-v2.7.0.json",
                 ".runtime/mcp/role-map.json", ".runtime/mcp/business.sqlite3"]
        value = {"captured_at": datetime.now(timezone.utc).isoformat(),
                 "sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
                 "original_counts": BlankStore(RUNTIME / "mcp/business.sqlite3").counts(),
                 "extension_counts": ExtensionStore(target / "mcp/extension.sqlite3").counts()}
        baseline.write_text(json.dumps(value, indent=2), encoding="utf-8")
    print("M1 private runtime prepared; original configuration preserved; no business input imported.")


if __name__ == "__main__":
    main()

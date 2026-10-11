"""Create private technical configuration without inventing user identities."""

import json
import secrets
from pathlib import Path

from private_runtime import ensure_private_directory


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".runtime"
    ensure_private_directory(runtime)
    token_file = runtime / "mcp-token.txt"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    token = token_file.read_text(encoding="utf-8").strip()
    (runtime / "mcp.env").write_text(f"GOV_MCP_TOKEN={token}\n", encoding="utf-8")
    mcp_runtime = runtime / "mcp"
    mcp_runtime.mkdir(exist_ok=True)
    (mcp_runtime / "mcp-token.txt").write_text(token, encoding="utf-8")
    roles = mcp_runtime / "role-map.json"
    old_roles = runtime / "role-map.json"
    if old_roles.exists() and not roles.exists():
        old_roles.replace(roles)
    if not roles.exists():
        roles.write_text("{}\n", encoding="utf-8")
    accounts = runtime / "accounts.json"
    existing = json.loads(accounts.read_text(encoding="utf-8")) if accounts.exists() else {}
    for role in ("tenant_admin", "developer", "citizen", "staff", "super_admin"):
        if role not in existing:
            existing[role] = {"email": "suadmin@nexent.com" if role == "super_admin" else f"ict-{role}@example.com",
                              "password": secrets.token_urlsafe(24) + "Aa1!"}
        if not (runtime / "nexent-state.json").exists() and existing[role]["email"].endswith("@example.test"):
            existing[role]["email"] = existing[role]["email"].replace("@example.test", "@example.com")
    accounts.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    from gov_mcp.core import BlankStore
    BlankStore(mcp_runtime / "business.sqlite3")
    print("Private runtime configuration prepared; role map remains empty until real account provisioning.")


if __name__ == "__main__":
    main()

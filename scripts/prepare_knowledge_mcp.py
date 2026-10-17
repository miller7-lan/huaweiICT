"""Prepare private read-only MCP identity configuration; never change existing agent bundles."""

import json
import secrets
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from private_runtime import ensure_private_directory


ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / ".runtime/knowledge-update"
    ensure_private_directory(target)
    KnowledgeStore(target, read_only=True)
    scope = json.loads((ROOT / "config/knowledge-scope.json").read_text(encoding="utf-8"))
    roles = json.loads((ROOT / ".runtime/m2/mcp/role-map.json").read_text(encoding="utf-8"))
    (target / "knowledge-role-map.json").write_text(json.dumps(roles), encoding="utf-8")
    (target / "knowledge-scope.json").write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")
    token_path = target / "knowledge-mcp-token.txt"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48), encoding="ascii")
    print(json.dumps({"prepared": True, "tools": ["govkb_get_environment_status", "govkb_search_evidence"],
                      "mapped_roles": sorted({v["role"] for users in roles.values() for v in users.values()}),
                      "existing_agents_changed": False, "business_review_completed": False}))


if __name__ == "__main__":
    main()

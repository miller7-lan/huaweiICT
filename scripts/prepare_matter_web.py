"""Prepare one private web runtime without contacting Nexent or a model."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal
from private_runtime import ensure_private_directory

PACK = ROOT / "data/real-matter-closure/nanjing/review-pack-v1"


def write(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime/real-matter-closure/matter-web-stage04")
    parser.add_argument("--auth", choices=("local-engineering", "nexent"), required=True)
    parser.add_argument("--role-map", type=Path)
    args = parser.parse_args()
    if args.runtime.exists() and any(args.runtime.iterdir()):
        parser.error("现有网页活动保留，不覆盖账户、会话或版本")
    if args.auth == "nexent" and (args.role_map is None or not args.role_map.is_file()):
        parser.error("Nexent模式需要已有的可信角色映射文件")
    ensure_private_directory(args.runtime)
    mode = "local_engineering_accounts" if args.auth == "local-engineering" else "nexent_accounts"
    if args.auth == "local-engineering":
        accounts, credentials = {}, {}
        for username, role in (("citizen", "citizen"), ("citizen-other", "citizen"),
                               ("staff", "staff"), ("staff-other", "staff"), ("maintainer", "maintainer")):
            password, salt = secrets.token_urlsafe(24), secrets.token_bytes(16)
            accounts[username] = {"id": "web-" + uuid.uuid4().hex, "role": role, "enabled": True,
                "salt": salt.hex(), "password_hash": hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000).hex()}
            credentials[username] = {"username": username, "password": password, "role": role}
        write(args.runtime / "local-accounts.json", {"mode": mode, "accounts": accounts})
        write(args.runtime / "login-credentials.json", {"purpose": "private_engineering_login_only", "accounts": credentials})
    else:
        role_map = json.loads(args.role_map.read_text(encoding="utf-8"))
        write(args.runtime / "nexent-roles.json", role_map)
    write(args.runtime / "web-settings.json", {"schema": 1, "auth_mode": mode,
        "nexent_url": "http://127.0.0.1:5010", "formal_business_publication": False})
    service = MatterLifecycle(args.runtime / "service")
    actor = Principal("web-technical-initializer", "maintainer", "service")
    versions = {}
    for stage in ("seed", "evolved"):
        candidate = service.prepare(PACK, ontology_stage=stage, principal=actor)
        versions[stage] = service.release_preview(candidate["candidate_id"], service.head(),
            note="Initial source-bound web engineering release; not human approval", principal=actor)["version"]
    write(args.runtime / "preparation.json", {"mode": mode, "versions": versions,
        "model_calls": 0, "knowledge_published": False})
    print(json.dumps({"prepared": True, "runtime": str(args.runtime), "authentication_mode": mode,
        "credential_file": str(args.runtime / "login-credentials.json") if args.auth == "local-engineering" else None,
        "credentials_printed": False, "model_calls": 0, "knowledge_published": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

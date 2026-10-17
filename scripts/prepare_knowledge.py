"""Prepare isolated maintenance credentials and source config; never touch M1 roles."""

import argparse
import json
import secrets
from pathlib import Path

from nexent_api import NexentAPI, ROOT, RUNTIME
from private_runtime import ensure_private_directory
from technical_controls import disable_technical_memory


def write(path, value):
    pending=path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    private = RUNTIME / ("knowledge-demo" if args.demo else "knowledge")
    ensure_private_directory(private)
    config_path = private / "heartbeat.json"
    if not config_path.exists():
        config = json.loads((ROOT / "config/knowledge/heartbeat.json").read_text(encoding="utf-8"))
        if args.demo:
            config.update(start_paused=True, demo_mode=True, sources=[{"id": "synthetic-policy", "title": "模拟身份证办事指南",
                "url": "http://govkb-demo:8775/fixture/policy", "format": "html", "body_selector": {"tag": "article", "id": "policy"},
                "allowed_hosts": ["govkb-demo"], "attachment_urls": [], "data_class": "synthetic", "enabled": True, "approved": True}])
        write(config_path, config)
    write(private / "classification.json", json.loads((ROOT / "config/preparation/classification.json").read_text(encoding="utf-8")))
    if not (private / "ontology.json").exists():
        write(private / "ontology.json",json.loads((ROOT / "config/preparation/ontology.seed.json").read_text(encoding="utf-8")))
    if not (private / "mcp-token.txt").exists(): (private / "mcp-token.txt").write_text(secrets.token_urlsafe(40), encoding="ascii")
    credentials_path = private / "service-account.json"
    if not credentials_path.exists():
        write(credentials_path, {"email": "govkb-service-"+secrets.token_hex(6)+"@example.com", "password": secrets.token_urlsafe(36)})
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    api, service = NexentAPI(), NexentAPI()
    try:
        api.login(accounts["tenant_admin"])
        try: user = service.login(credentials)
        except RuntimeError:
            invite = api.call("POST", "/invitations", {"tenant_id": state["tenant_id"], "code_type": "USER_INVITE",
                "group_ids": [state["group_id"]], "capacity": 1})["data"]["invitation_code"]
            service.call("POST", "/user/signup", {**credentials, "invite_code": invite, "auto_login": False})
            user = service.login(credentials)
        if user["tenant_id"] != state["tenant_id"] or user["user_role"] != "USER": raise RuntimeError("Dedicated service identity mismatch")
        disable_technical_memory(service)
        maintainer = api.login(accounts["developer"])
        write(private / "roles.json", {state["tenant_id"]: {user["user_id"]: "service", maintainer["user_id"]: "maintainer"}})
        print(json.dumps({"prepared": True, "runtime": str(private.relative_to(ROOT)), "dedicated_service_role": "USER", "sources_configured": len(json.loads(config_path.read_text(encoding='utf-8'))['sources'])}))
    finally:
        api.close()
        service.close()


if __name__ == "__main__": main()

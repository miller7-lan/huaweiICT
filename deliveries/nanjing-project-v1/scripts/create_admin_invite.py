"""User-started administrator invitation for manual registration in the local UI."""

import json
from urllib.parse import quote

from nexent_api import NexentAPI, RUNTIME, save_state
from private_runtime import ensure_private_directory


def create_invite(api, runtime=RUNTIME, persist_state=save_state):
    ensure_private_directory(runtime)
    accounts = json.loads((runtime / "accounts.json").read_text(encoding="utf-8"))
    user = api.login(accounts["super_admin"])
    if user.get("user_role") != "SU":
        raise RuntimeError("The bootstrap account must have the native SU role")
    path = runtime / "nexent-state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"accounts": {}, "models": {}}
    if "tenant_id" not in state:
        tenant = api.call("POST", "/tenants", {
            "tenant_name": "ICT空白工程验证", "skill_ids": [], "skill_names": [], "locale": "zh"
        })["data"]
        state.update(tenant_id=tenant["tenant_id"], group_id=int(tenant["default_group_id"]))
        persist_state(state)
    invitation_path = runtime / "manual-admin-invitation.json"
    if invitation_path.exists():
        previous = json.loads(invitation_path.read_text(encoding="utf-8"))
        if previous.get("tenant_id") == state["tenant_id"] and previous.get("code_type") == "ADMIN_INVITE":
            code = previous["invitation_code"]
            available = api.call("GET", "/invitations/" + quote(code, safe="") + "/available")["data"]["available"]
            if available:
                return code
    invitation = api.call("POST", "/invitations", {
        "tenant_id": state["tenant_id"], "code_type": "ADMIN_INVITE",
        "group_ids": [state["group_id"]], "capacity": 1
    })["data"]
    record = {"tenant_id": state["tenant_id"], "code_type": "ADMIN_INVITE",
              "invitation_code": invitation["invitation_code"], "capacity": 1}
    pending = invitation_path.with_suffix(".json.part")
    pending.write_text(json.dumps(record, indent=2), encoding="utf-8")
    pending.replace(invitation_path)
    return record["invitation_code"]


def main():
    api = NexentAPI()
    try:
        code = create_invite(api)
        print(f"Invitation code (one registration): {code}")
        print("Paste this code into the registration form at http://127.0.0.1:3000/zh.")
        print("Your email and password are entered by you in the UI; this script does not register them.")
    finally:
        api.close()


if __name__ == "__main__":
    main()

"""Create a fresh isolated USER and separate govx agents; do not republish baseline agents."""

import json
import secrets
from urllib.parse import quote

from nexent_api import NexentAPI, ROOT, RUNTIME, nonthinking_params
from technical_controls import disable_technical_memory


RENAMES = {"gov_coordinator": "govx_coordinator", "citizen_assistant": "govx_citizen_assistant",
           "staff_assistant": "govx_staff_assistant", "gov_blank_mcp": "govx_blank_mcp",
           "get_environment_status": "govx_get_environment_status", "search_evidence": "govx_search_evidence",
           "check_case_prerequisites": "govx_check_case_prerequisites", "政务空白链路主Agent": "M1隔离空白主Agent",
           "群众Agent": "M1群众Agent", "工作人员Agent": "M1工作人员Agent"}


def transform(value):
    if isinstance(value, str):
        for before, after in RENAMES.items():
            value = value.replace(before, after)
        return value
    if isinstance(value, dict):
        return {k: transform(v) for k, v in value.items()}
    if isinstance(value, list):
        return [transform(v) for v in value]
    return value


def save(path, value):
    pending = path.with_suffix(".part")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def main():
    target = RUNTIME / "m1"
    original = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    path = target / "nexent-state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "tenant_id": original["tenant_id"], "group_id": original["group_id"],
        "models": original["models"], "accounts": original["accounts"], "versions": {}}
    account_path = target / "accounts.json"
    isolated_accounts = json.loads(account_path.read_text(encoding="utf-8")) if account_path.exists() else dict(accounts)
    if "fresh_citizen" not in isolated_accounts:
        suffix = secrets.token_hex(4)
        isolated_accounts["fresh_citizen"] = {"email": f"m1-citizen-{suffix}@example.com", "password": secrets.token_urlsafe(32)}
        save(account_path, isolated_accounts)
    # EmailStr rejects reserved .test domains in the pinned native API.
    if isolated_accounts["fresh_citizen"]["email"].endswith("@example.test"):
        isolated_accounts["fresh_citizen"]["email"] = isolated_accounts["fresh_citizen"]["email"].replace("@example.test", "@example.com")
        save(account_path, isolated_accounts)
    api, citizen_api = NexentAPI(), NexentAPI()
    try:
        api.login(accounts["tenant_admin"])
        if "fresh_citizen" not in state["accounts"]:
            credentials = isolated_accounts["fresh_citizen"]
            try:
                identity = citizen_api.login(credentials)
            except RuntimeError:
                invite = api.call("POST", "/invitations", {"tenant_id": state["tenant_id"], "code_type": "USER_INVITE",
                    "group_ids": [state["group_id"]], "capacity": 1})["data"]["invitation_code"]
                citizen_api.call("POST", "/user/signup", {**credentials, "invite_code": invite, "auto_login": False})
                identity = citizen_api.login(credentials)
            if identity["tenant_id"] != state["tenant_id"] or identity["user_role"] != "USER":
                raise RuntimeError("Fresh account identity mismatch")
            disable_technical_memory(citizen_api)
            state["accounts"]["fresh_citizen"] = {"user_id": identity["user_id"], "platform_role": identity["user_role"]}
            save(path, state)
        roles_path = target / "mcp/role-map.json"
        roles = json.loads(roles_path.read_text(encoding="utf-8"))
        roles[state["tenant_id"]][state["accounts"]["fresh_citizen"]["user_id"]] = {"role": "citizen", "knowledge_maintainer": False}
        save(roles_path, roles)
        api.login(accounts["developer"])
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        if not any(m.get("remote_mcp_server_name", m.get("mcp_name")) == "govx_blank_mcp" for m in services):
            token = (target / "mcp-token.txt").read_text(encoding="ascii").strip()
            api.call("POST", "/mcp/add", {"name": "govx_blank_mcp", "server_url": "http://govx-blank-mcp:8766/sse",
                "authorization_token": "Bearer " + token, "enabled": True,
                "group_ids": str(state["group_id"]), "ingroup_permission": "READ_ONLY"})
        api.call("GET", "/tool/scan_tool")
        bundle = transform(json.loads((ROOT / "config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8")))
        save(ROOT / "config/agents.m1.nexent-v2.7.0.json", bundle)
        if "agents" not in state:
            agents = api.call("GET", "/agent/list?tenant_id=" + quote(state["tenant_id"], safe=""))
            if isinstance(agents, dict):
                agents = agents.get("data", agents.get("agents", []))
            wanted = {a["name"] for a in bundle["agent_info"].values()}
            found = {a["name"]: a["agent_id"] for a in agents if a["name"] in wanted}
            if found and set(found) != wanted:
                raise RuntimeError("Partial M1 agent import; inspect before retrying")
            if not found:
                imported = api.call("POST", "/agent/import", {"agent_info": bundle, "force_import": False})
                found = {a["name"]: imported["agent_id_mapping"][k] for k, a in bundle["agent_info"].items()}
            state["agents"] = found
            save(path, state)
        model = "gov-qwen3-4b:ctx8192-v1"
        prompts = {a["name"]: a for a in bundle["agent_info"].values()}
        for name in ("govx_citizen_assistant", "govx_staff_assistant", "govx_coordinator"):
            if name in state["versions"]:
                continue
            aid = state["agents"][name]
            api.call("POST", "/agent/update", {"agent_id": aid, "author": "ICT 项目组", "group_ids": [state["group_id"]],
                "ingroup_permission": "READ_ONLY", "model_ids": [state["models"][model]],
                **{k: prompts[name].get(k, "") for k in ("duty_prompt", "constraint_prompt", "few_shots_prompt")},
                "model_params_override": {str(state["models"][model]): {"temperature": .1, "extra_params": nonthinking_params()}},
                "requested_output_tokens": 1024, "allow_chat_metadata": False, "enable_protocol_repair_retry": False})
            state["versions"][name] = api.call("POST", f"/agent/{aid}/publish", {
                "version_name": "m1-blank", "release_note": "Isolated M1 empty technical verification"})["version_no"]
            save(path, state)
        state["active_model"] = model
        save(path, state)
        print("Fresh M1 USER and separate govx agents configured; original agents and private baseline unchanged.")
    finally:
        api.close()
        citizen_api.close()


if __name__ == "__main__":
    main()

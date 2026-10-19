"""Provision isolated native accounts and the three-agent graph after deployment."""

import argparse
import json
from urllib.parse import quote

from nexent_api import NexentAPI, ROOT, RUNTIME, activate_model, nonthinking_params, save_state
from technical_controls import disable_technical_memory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-url", default="http://127.0.0.1:5010")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:5014")
    args = parser.parse_args()
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    path = RUNTIME / "nexent-state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"accounts": {}, "models": {}}
    api = NexentAPI(args.config_url, args.runtime_url)
    try:
        api.login(accounts["super_admin"])
        if "tenant_id" not in state:
            tenant = api.call("POST", "/tenants", {"tenant_name": "ICT空白工程验证", "skill_ids": [], "skill_names": [], "locale": "zh"})["data"]
            state.update(tenant_id=tenant["tenant_id"], group_id=int(tenant["default_group_id"]))
            save_state(state)
        for role, invitation_type in (("tenant_admin", "ADMIN_INVITE"), ("developer", "DEV_INVITE"), ("citizen", "USER_INVITE"), ("staff", "USER_INVITE")):
            if role in state["accounts"]:
                continue
            # Re-login on recovery: a successful signup can precede checkpoint persistence.
            account_api = NexentAPI(args.config_url)
            try:
                try:
                    info = account_api.login(accounts[role])
                except RuntimeError:
                    invite = api.call("POST", "/invitations", {"tenant_id": state["tenant_id"], "code_type": invitation_type,
                                "group_ids": [state["group_id"]], "capacity": 1})["data"]["invitation_code"]
                    account_api.call("POST", "/user/signup", {**accounts[role], "invite_code": invite, "auto_login": False})
                    info = account_api.login(accounts[role])
                if info["tenant_id"] != state["tenant_id"]:
                    raise RuntimeError("Existing account belongs to a different tenant")
                state["accounts"][role] = {"user_id": info["user_id"], "platform_role": info["user_role"]}
                save_state(state)
            finally:
                account_api.close()
            if role == "tenant_admin":
                api.login(accounts[role])
        api.login(accounts["tenant_admin"])
        existing = api.call("GET", "/model/list")["data"]
        for model in ("gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1"):
            matches = [m for m in existing if m["display_name"] == model]
            settings = {"context_window_tokens": 8192, "max_output_tokens": 1024,
                        "default_output_reserve_tokens": 1024, "temperature": .1, "concurrency_limit": 1,
                        "extra_params": nonthinking_params()}
            if not matches:
                api.call("POST", "/model/create", {"model_factory": "OpenAI-API-Compatible", "model_name": model,
                    "display_name": model, "model_type": "llm", "base_url": "http://host.docker.internal:11434/v1",
                    "api_key": "ollama", **settings})
                existing = api.call("GET", "/model/list")["data"]
                matches = [m for m in existing if m["display_name"] == model]
            else:
                # Recovery also pins existing native rows, including thinking
                # defaults that would otherwise leak into an unknown model alias.
                api.call("POST", "/model/update?display_name=" + quote(model, safe=""), settings)
            if len(matches) != 1:
                raise RuntimeError("Native model name is missing or ambiguous")
            state["models"][model] = matches[0]["model_id"]
            save_state(state)
            health = api.call("POST", "/model/healthcheck?display_name=" + quote(model, safe="") + "&model_type=llm")
            if health.get("data", {}).get("connectivity") is not True:
                raise RuntimeError("Native model healthcheck failed; published agents would be hidden from the UI")
        api.login(accounts["developer"])
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        if not any(m.get("remote_mcp_server_name", m.get("mcp_name")) == "gov_blank_mcp" for m in services):
            token = (RUNTIME / "mcp-token.txt").read_text(encoding="utf-8").strip()
            api.call("POST", "/mcp/add", {"name": "gov_blank_mcp", "server_url": "http://gov-blank-mcp:8765/sse",
                "authorization_token": "Bearer " + token, "enabled": True,
                "group_ids": str(state["group_id"]), "ingroup_permission": "READ_ONLY"})
        api.call("GET", "/tool/scan_tool")
        if "agents" not in state:
            # Recover a prior completed import by exact names before creating new resources.
            agents = api.call("GET", "/agent/list?tenant_id=" + quote(state["tenant_id"], safe=""))
            if isinstance(agents, dict):
                agents = agents.get("data", agents.get("agents", []))
            wanted = {"gov_coordinator", "citizen_assistant", "staff_assistant"}
            found = {a["name"]: a["agent_id"] for a in agents if a["name"] in wanted}
            if found and set(found) != wanted:
                raise RuntimeError("Partial native import detected; inspect existing agents before retrying")
            if not found:
                bundle = json.loads((ROOT / "config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
                imported = api.call("POST", "/agent/import", {"agent_info": bundle, "force_import": False})
                found = {a["name"]: imported["agent_id_mapping"][key] for key, a in bundle["agent_info"].items()}
            state["agents"] = found
            save_state(state)
        for agent_id in state["agents"].values():
            api.call("POST", "/agent/update", {"agent_id": agent_id, "author": "ICT 项目组", "group_ids": [state["group_id"]], "ingroup_permission": "READ_ONLY"})
        roles = {state["tenant_id"]: {state["accounts"][r]["user_id"]: r for r in ("citizen", "staff")}}
        roles[state["tenant_id"]][state["accounts"]["developer"]["user_id"]] = "citizen"
        # Tenant administrator remains unmapped for the negative role test.
        pending = RUNTIME / "mcp/role-map.json.part"
        pending.write_text(json.dumps(roles, indent=2), encoding="utf-8")
        pending.replace(RUNTIME / "mcp/role-map.json")
        for role in ("tenant_admin", "developer", "citizen", "staff"):
            api.login(accounts[role])
            disable_technical_memory(api)
        api.login(accounts["developer"])
        activate_model(api, state, "gov-qwen3-4b:ctx8192-v1")
        print("Native provisioning completed; credentials remain in .runtime/accounts.json.")
    finally:
        api.close()


if __name__ == "__main__":
    main()

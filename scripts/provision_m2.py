"""Import independent M2 agents. Never republish the original or M1 agents."""

import argparse
import json
from urllib.parse import quote

from nexent_api import NexentAPI, ROOT, RUNTIME, nonthinking_params
from provision_m1 import save


def bundle():
    template = json.loads((ROOT / "config/agents.m1.nexent-v2.7.0.json").read_text(encoding="utf-8"))
    value = json.loads(json.dumps(template, ensure_ascii=False).replace("govx_", "govm2_").replace("govm2_blank_mcp", "govm2_preview_mcp")
                       .replace("M1隔离空白主Agent", "M2事项协作主Agent").replace("M1群众Agent", "M2群众引导Agent")
                       .replace("M1工作人员Agent", "M2工作人员核验Agent"))
    constraint = ("只运行M2工程预览的真实工具链。tenant_id、user_id由真实Nexent会话注入，禁止覆盖身份、角色、版本或工具结果。"
        "所有官方来源仍待业务复核，材料是模拟存在声明。保留引用、版本、待复核项、notice和formal_approval=false。"
        "EMPTY/BLOCKED/ERROR立即停止；禁止编造政策、申请人事实、材料或审批结论。"
        "执行一次完整Python动作，用实际返回值final_answer；禁止多次工具调用、循环或规划器。")
    root_action = '''import json
environment = govm2_get_environment_status()
if isinstance(environment, str):
    environment = json.loads(environment)
if environment["status"] != "OK":
    answer = environment
elif environment["data"]["role"] == "citizen":
    answer = govm2_citizen_assistant(task="只调用一次govm2_search_evidence()，原样返回真实结果。")
elif environment["data"]["role"] == "staff":
    answer = govm2_staff_assistant(task="只调用一次govm2_check_case_prerequisites()，原样返回真实结果。")
else:
    answer = environment
final_answer(answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False))'''
    for aid, tool in (("1", "govm2_get_environment_status"), ("2", "govm2_search_evidence"), ("3", "govm2_check_case_prerequisites")):
        agent = value["agent_info"][aid]
        action = root_action if aid == "1" else ("import json\nanswer = " + tool + "()\nif isinstance(answer, str):\n    answer = json.loads(answer)\nfinal_answer(json.dumps(answer, ensure_ascii=False))")
        instruction = "Use exactly one complete action below. Keep the actual tool result; do not output a literal sample dictionary or a narrative.\n<code>\n" + action + "\n</code>"
        agent.update(description="M2工程预览，按真实角色运行并保留工具引用和待复核状态。", duty_prompt=instruction,
            few_shots_prompt=instruction, constraint_prompt=constraint, requested_output_tokens=1024)
        agent["tools"] = [{"class_name": tool, "name": tool, "source": "mcp", "usage": "govm2_preview_mcp", "params": {}}]
    value["mcp_info"] = [{"mcp_server_name": "govm2_preview_mcp", "mcp_url": "http://govm2-mcp:8770/sse"}]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Republish only the independently named M2 agents")
    args = parser.parse_args()
    target = RUNTIME / "m2"
    original = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((target / "accounts.json").read_text(encoding="utf-8"))
    path = target / "nexent-state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "tenant_id": original["tenant_id"], "group_id": original["group_id"], "models": original["models"],
        "accounts": {r: original["accounts"][r] for r in ("citizen", "staff")}, "versions": {}}
    api = NexentAPI(runtime_base="http://127.0.0.1:5018")
    try:
        api.login(accounts["developer"])
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        if not any(m.get("remote_mcp_server_name", m.get("mcp_name")) == "govm2_preview_mcp" for m in services):
            token = (target / "mcp-token.txt").read_text(encoding="ascii").strip()
            api.call("POST", "/mcp/add", {"name": "govm2_preview_mcp", "server_url": "http://govm2-mcp:8770/sse",
                "authorization_token": "Bearer " + token, "enabled": True,
                "group_ids": str(state["group_id"]), "ingroup_permission": "READ_ONLY"})
        api.call("GET", "/tool/scan_tool")
        config = bundle()
        save(ROOT / "config/agents.m2.nexent-v2.7.0.json", config)
        if "agents" not in state:
            listed = api.call("GET", "/agent/list?tenant_id=" + quote(state["tenant_id"], safe=""))
            if isinstance(listed, dict):
                listed = listed.get("data", listed.get("agents", []))
            wanted = {a["name"] for a in config["agent_info"].values()}
            found = {a["name"]: a["agent_id"] for a in listed if a["name"] in wanted}
            if found and set(found) != wanted:
                raise RuntimeError("Partial M2 import; inspect before retrying")
            if not found:
                imported = api.call("POST", "/agent/import", {"agent_info": config, "force_import": False})
                found = {a["name"]: imported["agent_id_mapping"][key] for key, a in config["agent_info"].items()}
            state["agents"] = found
            save(path, state)
        model = original["active_model"]
        prompts = {a["name"]: a for a in config["agent_info"].values()}
        for name in ("govm2_citizen_assistant", "govm2_staff_assistant", "govm2_coordinator"):
            aid = state["agents"][name]
            if name in state["versions"] and not args.refresh:
                continue
            api.call("POST", "/agent/update", {"agent_id": aid, "author": "ICT 项目组", "group_ids": [state["group_id"]],
                "display_name": prompts[name]["display_name"],
                "ingroup_permission": "READ_ONLY", "model_ids": [state["models"][model]],
                **{k: prompts[name][k] for k in ("duty_prompt", "constraint_prompt", "few_shots_prompt")},
                "model_params_override": {str(state["models"][model]): {"temperature": .1, "extra_params": nonthinking_params()}},
                "requested_output_tokens": 1024, "allow_chat_metadata": False, "enable_protocol_repair_retry": False})
            state["versions"][name] = api.call("POST", f"/agent/{aid}/publish", {
                "version_name": "m2-preview", "release_note": "Small classified source preview; business review pending"})["version_no"]
            save(path, state)
        state["active_model"] = model
        save(path, state)
        print("Independent M2 agents published; source review remains pending.")
    finally:
        api.close()


if __name__ == "__main__":
    main()

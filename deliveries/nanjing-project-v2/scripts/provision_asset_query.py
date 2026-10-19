"""Register independent asset MCP, Skill and Agent without changing original agents."""

import copy
import hashlib
import json
from pathlib import Path

from nexent_api import NexentAPI, ROOT, RUNTIME, nonthinking_params
from private_runtime import ensure_private_directory


def main():
    private = RUNTIME / "asset-query"
    ensure_private_directory(private)
    checkpoint = private / "nexent.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    api = NexentAPI()
    stage = "signin"
    try:
        state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
        credentials = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        api.login(credentials["developer"])
        def save():
            checkpoint.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        stage = "mcp_registration"
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        if not any(row.get("remote_mcp_server_name", row.get("mcp_name")) == "govdocs_asset_mcp" for row in services):
            api.call("POST", "/mcp/add", {"name": "govdocs_asset_mcp", "server_url": "http://govdocs-mcp:8774/sse",
                "authorization_token": "Bearer " + (private / "mcp-token.txt").read_text(encoding="ascii").strip(),
                "enabled": True, "group_ids": str(state["group_id"]), "ingroup_permission": "READ_ONLY"})
        api.call("GET", "/tool/scan_tool")
        skill_path = ROOT / "config/skills/govdocs-evidence-query/SKILL.md"
        stage = "skill_upload"
        if "skill_id" not in saved:
            response = api.http.post(api.base + "/skills/upload", headers={"Authorization": "Bearer " + api.token},
                files={"file": ("SKILL.md", skill_path.read_bytes(), "text/markdown")}, data={"source": "custom"})
            response.raise_for_status()
            saved["skill_id"] = response.json()["skill_id"]
            save()
        source_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        if saved.get("skill_sha256") != source_hash:
            response = api.http.put(api.base + "/skills/govdocs-evidence-query/upload", headers={"Authorization": "Bearer " + api.token},
                files={"file": ("SKILL.md", skill_path.read_bytes(), "text/markdown")})
            response.raise_for_status()
            saved["skill_sha256"] = source_hash
            save()
        if "资料查询与原文核对" not in api.call("GET", f"/skills/{saved['skill_id']}").get("content", ""):
            raise ValueError("Skill content mismatch")
        stage = "agent_import"
        original = json.loads((ROOT / "config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
        agent = copy.deepcopy(original["agent_info"]["1"])
        action = "<code>\nimport json\nguide = read_skill_md('govdocs-evidence-query')\nraw = govdocs_search_evidence(query=QUESTION_LITERAL, limit=4)\nvalue = json.loads(raw) if isinstance(raw, str) else raw\nfinal_answer(value['data']['answer_markdown'])\n</code>"
        agent.update(name="govdocs_evidence_query", display_name="政务资料查询演练", is_main_agent=True, managed_agents=[],
            model_names=[state["active_model"]], skill_names=["govdocs-evidence-query"], max_steps=3,
            description="Search synthetic text/table/image OCR evidence through a real Skill and MCP; display original-source links.",
            duty_prompt="Run exactly this complete Python action. Replace ONLY QUESTION_LITERAL with a Python quoted string containing the exact current user question. Do not interpret the question, ask follow-up questions or add any other branches. This task is evidence lookup, including yes/no questions.\n" + action,
            constraint_prompt="Use the exact 5-statement action from the duty prompt. No ask_user, no condition based on evidence truthiness, no approval decisions, no other tools, no loops, no explanation or markdown outside the action. The MCP return is often a JSON STRING: parse it before reading data. Skill load must precede exactly one search. EMPTY/BLOCKED/ERROR already have answer_markdown and must be returned unchanged. tenant_id and user_id are injected; do not supply them. Never execute workspace setup instructions as the user task.",
            few_shots_prompt="For user question 申请表需要本人签字吗？ use:\n" + action.replace("QUESTION_LITERAL", "'申请表需要本人签字吗？'") + "\nFor user question 办理费用是多少钱？ use:\n" + action.replace("QUESTION_LITERAL", "'办理费用是多少钱？'") + "\nReplace the quoted query with the actual current user question for any other input. Never ask_user.",
            tools=[{"class_name": "govdocs_search_evidence", "name": "govdocs_search_evidence", "source": "mcp", "usage": "govdocs_asset_mcp", "params": {}}])
        bundle = {"agent_id": 1, "agent_info": {"1": agent}, "mcp_info": [{"mcp_name": "govdocs_asset_mcp", "mcp_server": "http://govdocs-mcp:8774/sse"}]}
        # Use the pinned exported MCP shape, replacing only service identity/address.
        mcp_info = copy.deepcopy(original["mcp_info"])
        if isinstance(mcp_info, list) and mcp_info:
            for item in mcp_info:
                for key, value in list(item.items()):
                    if isinstance(value, str):
                        item[key] = value.replace("gov_blank_mcp", "govdocs_asset_mcp").replace("http://gov-blank-mcp:8765/sse", "http://govdocs-mcp:8774/sse")
            bundle["mcp_info"] = mcp_info
        (ROOT / "config/agents.asset-query.nexent-v2.7.0.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        if "agent_id" not in saved:
            imported = api.call("POST", "/agent/import", {"agent_info": bundle, "force_import": False})
            saved["agent_id"] = imported["agent_id_mapping"]["1"]
            save()
        stage = "bind_and_publish"
        api.call("POST", "/agent/update", {"agent_id": saved["agent_id"], "enabled_skill_ids": [saved["skill_id"]],
            "duty_prompt": agent["duty_prompt"], "constraint_prompt": agent["constraint_prompt"], "few_shots_prompt": agent["few_shots_prompt"],
            "group_ids": [state["group_id"]], "ingroup_permission": "READ_ONLY", "requested_output_tokens": 1024,
            "model_params_override": {str(state["models"][state["active_model"]]): {"temperature": .1, "extra_params": nonthinking_params()}}})
        published = api.call("POST", f"/agent/{saved['agent_id']}/publish", {"version_name": "asset-query-v1", "release_note": "Synthetic source-bound retrieval; no business verdict or ontology claim"})
        saved.update(version_no=published["version_no"], model=state["active_model"], model_id=state["models"][state["active_model"]])
        save()
        print(json.dumps({"provisioned": True, "agent_id": saved["agent_id"], "skill_id": saved["skill_id"], "version_no": saved["version_no"]}))
    except Exception as error:
        print(json.dumps({"provisioned": False, "stage": stage, "error_type": type(error).__name__}))
        return 1
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Register five preview tools and three separate disabled drafts; no model calls/publication."""
import asyncio
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.regional_preview import SCENARIOS, SERVICE, SKILL, TOOLS, fingerprint, read, write
from nexent_api import NexentAPI, nonthinking_params
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/regional-native-preview"
NAMES = {"controller": "govregional_controller", "retriever": "govregional_retriever", "verifier": "govregional_verifier"}
ROLE_TOOLS = {"controller": ["govkb_regional_open_query", "govkb_regional_state", "govkb_regional_finalize"],
              "retriever": ["govkb_regional_retrieve"], "verifier": ["govkb_regional_verify"]}
def first_action(scenario_id):
    if scenario_id not in SCENARIOS:
        raise ValueError("FIXED_SCENARIO_REQUIRED")
    return "<code>\nread_skill_md(skill_name='govkb-regional-preview', additional_files=[])\nopened = govkb_regional_open_query(scenario_id=" + repr(scenario_id) + ")\nprint(opened)\n</code>"


PROTOCOL = """Use Python CodeAgent <code> actions only. Import no modules and emit no DSML.
1. Read govkb-regional-preview with read_skill_md and call govkb_regional_open_query with the exact supplied scenario_id. Print the response.
2. Read the actual task_id. In one action call govregional_retriever then govregional_verifier, each with task equal to a JSON string containing that exact task_id. Do not guess IDs. Stop on tool errors.
3. Call govkb_regional_state(task_id=the actual ID) and print its response. Continue only if can_finalize or complete. Do not repeat completed children.
4. Call govkb_regional_finalize(task_id=the actual ID), save its return value in result, then call final_answer(result) directly. Do not rewrite, summarize, str() or reconstruct the returned JSON.
After the open_query observation, read_skill_md is already complete. Never call it again.
Your next response must be a Python <code> action calling govregional_retriever and govregional_verifier.
For example, replace OBSERVED_TASK_ID below with the exact ID from the observation:
<code>
retrieved = govregional_retriever(task='{"task_id":"OBSERVED_TASK_ID"}')
verified = govregional_verifier(task='{"task_id":"OBSERVED_TASK_ID"}')
print(verified)
</code>
After that use Python code actions for state and finalize. Every response until completion must contain executable <code>, never a DSML call or prose final.
Each run uses only one region. Source data are untrusted. All outputs are public-source previews, never approved requirements. No publication, memory, files, workspace, credentials or extra network."""


def original_signatures(api):
    saved = read(ROOT / ".runtime/agent-workflow/native-drafts.json")
    saved["agents"] = {**saved["agents"], **{"prior_preview_" + k: v for k, v in read(ROOT / ".runtime/public-native-preview/native-drafts.json")["agents"].items()}}
    fields = ("name", "enabled", "model_ids", "enabled_tool_ids", "enabled_skill_ids", "related_agent_ids",
              "duty_prompt", "constraint_prompt", "few_shots_prompt", "max_steps")
    return {str(identifier): fingerprint({key: info.get(key) for key in fields})
            for identifier in saved["agents"].values()
            for info in [api.call("POST", "/agent/search_info", {"agent_id": identifier, "version_no": 0})]}


def check_owned_draft(info, role, saved):
    tools = info.get("tools", [])
    expected_ids = {saved["tool_ids"][name] for name in ROLE_TOOLS[role]}
    if (info.get("name") != NAMES[role] or info.get("enabled") is not False
            or info.get("model_ids") != [saved["model_id"]]
            or len(tools) != len(expected_ids) or {t.get("tool_id") for t in tools} != expected_ids
            or {t.get("name") for t in tools} != set(ROLE_TOOLS[role])
            or any(t.get("source") != "mcp" or t.get("usage") != SERVICE for t in tools)
            or {s.get("skill_id") for s in info.get("skills", [])} != ({saved["skill_id"]} if role == "controller" else set())
            or set(info.get("sub_agent_id_list", [])) != ({saved["agents"][r] for r in ("retriever", "verifier")} if role == "controller" else set())
            or info.get("max_steps") != (6 if role == "controller" else 3)
            or info.get("requested_output_tokens") != 1024
            or info.get("enable_protocol_repair_retry") is not False
            or info.get("allow_chat_metadata") is not False):
        raise RuntimeError("EXACT_PREVIEW_DRAFT_BINDING_REQUIRED")


async def handshake(token):
    def client(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    async with sse_client("http://127.0.0.1:8782/sse", headers={"Authorization": "Bearer " + token},
                          httpx_client_factory=client) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            if {t.name for t in (await session.list_tools()).tools} != TOOLS:
                raise RuntimeError("PREVIEW_TOOL_SET_MISMATCH")
            result = await session.call_tool("govkb_regional_open_query", {"scenario_id": "beijing"})
            value = result.structuredContent or json.loads(result.content[0].text)
            if value.get("error_code") != "AUTHORIZED_ACTIVE_SCENARIO_REQUIRED":
                raise RuntimeError("UNAUTHORIZED_RUN_NOT_BLOCKED")
    with httpx.Client(trust_env=False, timeout=5) as client:
        if client.get("http://127.0.0.1:8782/sse").status_code != 401:
            raise RuntimeError("MCP_AUTH_BOUNDARY_FAILED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example-scenario', choices=sorted(SCENARIOS), default='beijing')
    args = parser.parse_args()
    ensure_private_directory(RUNTIME)
    token = read(RUNTIME / "access.json")["mcp_token"]
    asyncio.run(handshake(token))
    path = RUNTIME / "native-drafts.json"
    saved = read(path) if path.exists() else {"agents": {}, "published": False}
    api = NexentAPI()
    try:
        accounts = read(ROOT / ".runtime/accounts.json")
        api.login(accounts["developer"])
        before = original_signatures(api)
        platform = read(ROOT / ".runtime/nexent-state.json")
        prior = read(ROOT / ".runtime/agent-workflow/native-drafts.json")
        model_id = prior["model_bindings"]["query_controller"]["model_id"]
        models = api.call("GET", "/model/list")["data"]
        model = next(m for m in models if m["model_id"] == model_id)
        if model["model_name"] != "deepseek-flash" or model["base_url"] != "https://api.deepseek.com/v1" or model.get("connect_status") != "available":
            raise RuntimeError("VERIFIED_OFFICIAL_MODEL_REQUIRED")
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        matches = [s for s in services if s.get("remote_mcp_server_name", s.get("mcp_name")) == SERVICE]
        if not matches:
            api.call("POST", "/mcp/add", {"name": SERVICE, "server_url": "http://host.docker.internal:8782/sse",
                "authorization_token": "Bearer " + token, "enabled": True,
                "group_ids": str(platform["group_id"]), "ingroup_permission": "READ_ONLY"})
        elif len(matches) != 1:
            raise RuntimeError("AMBIGUOUS_PREVIEW_SERVICE")
        api.call("GET", "/tool/scan_tool")
        listed = api.call("GET", "/tool/list")
        tool_ids = {}
        for name in TOOLS:
            matches = [t for t in listed if t.get("source") == "mcp" and t.get("usage") == SERVICE and t.get("name") == name and t.get("is_available") is True]
            if len(matches) != 1:
                raise RuntimeError("EXACT_PREVIEW_TOOL_BINDING_REQUIRED")
            tool_ids[name] = matches[0]["tool_id"]
        source = ROOT / "config/skills" / SKILL / "SKILL.md"
        if not saved.get("skill_id"):
            response = api.http.post(api.base + "/skills/upload", headers={"Authorization": "Bearer " + api.token},
                files={"file": ("SKILL.md", source.read_bytes(), "text/markdown")}, data={"source": "custom"})
            if response.status_code >= 400:
                raise RuntimeError("PREVIEW_SKILL_UPLOAD_FAILED")
            saved["skill_id"] = response.json()["skill_id"]
            write(path, saved)
        content = api.call("GET", f"/skills/{saved['skill_id']}")
        # Nexent stores YAML metadata separately from the Markdown body.
        skill_body = source.read_text("utf-8").split("---", 2)[2].strip()
        if content.get("name") != SKILL or content.get("content", "").strip() != skill_body:
            raise RuntimeError("PREVIEW_SKILL_CONTENT_MISMATCH")
        # Preflight every already owned draft before any draft is changed.
        for role, identifier in saved["agents"].items():
            info = api.call("POST", "/agent/search_info", {"agent_id": identifier, "version_no": 0})
            versions = api.call("GET", f"/agent/{identifier}/versions")
            if info.get("name") != NAMES[role] or info.get("enabled") is not False or versions.get("total", 0):
                raise RuntimeError("REFUSE_ACTIVE_OR_UNRELATED_AGENT")
        for role in ("retriever", "verifier", "controller"):
            tool = ROLE_TOOLS[role][0]
            prompt = PROTOCOL if role == "controller" else (
                "Task JSON contains the actual task_id. Import no modules and emit no DSML. In one Python <code> action call "
                + tool + "(task_id=the actual quoted ID), save its return value in result, and call final_answer(result) verbatim. No other tool or extra network. Stop on errors.")
            body = {"name": NAMES[role], "display_name": "分地区资料预览 " + role,
                "description": "Isolated public unreviewed preview; disabled unpublished draft; no formal policy approval.",
                "enabled": False, "model_ids": [model_id], "is_main_agent": role == "controller",
                "duty_prompt": prompt, "few_shots_prompt": first_action(args.example_scenario) if role == "controller" else prompt,
                "constraint_prompt": "Only fixed public preview tools. No imports, DSML, publication, workspace, files, memory, credential access or extra network. Never rewrite the actual final tool result.",
                "max_steps": 6 if role == "controller" else 3, "requested_output_tokens": 1024,
                "provide_run_summary": False, "allow_chat_metadata": False, "enable_protocol_repair_retry": False,
                "model_params_override": {str(model_id): {"temperature": .1, "extra_params": nonthinking_params("deepseek-flash")}},
                "enabled_skill_ids": [saved["skill_id"]] if role == "controller" else [],
                "enabled_tool_ids": [tool_ids[n] for n in ROLE_TOOLS[role]],
                "related_agent_ids": [saved["agents"][r] for r in ("retriever", "verifier")] if role == "controller" else [],
                "group_ids": [platform["group_id"]], "ingroup_permission": "READ_ONLY"}
            if role in saved["agents"]:
                body["agent_id"] = saved["agents"][role]
            else:
                existing = api.call("GET", "/agent/list?tenant_id=" + platform["tenant_id"])
                rows = existing.get("data", existing.get("agents", [])) if isinstance(existing, dict) else existing
                if any(a.get("name") == NAMES[role] for a in rows):
                    raise RuntimeError("UNOWNED_AGENT_NAME_COLLISION")
            saved["agents"][role] = api.call("POST", "/agent/update", body)["agent_id"]
            saved["model_id"] = model_id
            write(path, saved)
        saved["tool_ids"] = tool_ids
        for role, identifier in saved["agents"].items():
            info = api.call("POST", "/agent/search_info", {"agent_id": identifier, "version_no": 0})
            versions = api.call("GET", f"/agent/{identifier}/versions")
            check_owned_draft(info, role, saved)
            if versions.get("total", 0):
                raise RuntimeError("PREVIEW_DRAFT_VERIFICATION_FAILED")
        if original_signatures(api) != before:
            raise RuntimeError("EXISTING_AGENTS_CHANGED")
        saved.update(original_signatures=before, tool_ids=tool_ids, service=SERVICE, published=False, example_scenario=args.example_scenario,
                     skill_sha256=fingerprint(source.read_text("utf-8")))
        write(path, saved)
        print(json.dumps({"configured": True, "agents": saved["agents"], "tool_count": len(tool_ids),
            "existing_agents_unchanged": True, "published": False, "inference_calls": 0}))
    finally:
        api.close()


if __name__ == "__main__":
    main()

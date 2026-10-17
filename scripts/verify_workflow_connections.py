"""Verify real platform/MCP wiring using metadata and program tools only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import subprocess

import httpx

from nexent_api import NexentAPI, ROOT, RUNTIME
from provision_agent_workflow import TOOLS, NAMES
sys.path.insert(0, str(ROOT/"src"))
from gov_mcp.decision.connections import Connections
from gov_mcp.decision.workflow import Workflow


def main():
    runtime = RUNTIME/"agent-workflow"
    saved = json.loads((runtime/"native-drafts.json").read_text("utf-8"))
    access = json.loads((runtime/"access.json").read_text("utf-8"))
    credentials = json.loads((RUNTIME/"accounts.json").read_text("utf-8"))
    checks = []
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed: raise AssertionError(name)
    config = Connections(ROOT/"config/decision/connections.json")
    probe = config.probe()
    check("本机 Nexent 两个服务端实际就绪", all(s["connected"] for s in probe["services"].values()))
    cloud_roles = [r for r in probe["roles"] if r["role"] != "jev"]
    check("六个语言模型角色统一指向云端 DeepSeek", len(cloud_roles) == 6 and all(r["transport"] == "deepseek" for r in cloud_roles))
    check("总推理开关关闭，配置仅保存凭据引用", not probe["inference_enabled"] and all(r["credential_ref"] == "env:DEEPSEEK_API_KEY" for r in cloud_roles))
    facts = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}
    before = Workflow(runtime).query(facts, "branch-adult-self")
    before_paused = {r["requirement_id"] for r in before["paused"]}
    native = []
    api = NexentAPI()
    try:
        api.login(credentials["developer"])
        tools = api.call("GET", "/tool/list")
        registered = {t["name"] for t in tools if t.get("usage") == saved["mcp_service"] and t.get("is_available")}
        check("新服务的工具实际登记且不包含正式发布工具", {t for group in TOOLS.values() for t in group} <= registered and not any("publish" in t or "rollback" in t for t in registered))
        for role, agent_id in saved["agents"].items():
            info = api.call("POST", "/agent/search_info", {"agent_id": agent_id, "version_no": 0})
            versions = api.call("GET", f"/agent/{agent_id}/versions")
            bound = {t["name"] for t in info["tools"] if t.get("enabled")}
            check(role+"：原生草稿禁用、无模型、无发布版本、工具正确", info["name"] == NAMES[role] and info["enabled"] is False and not info["model_ids"] and versions["total"] == 0 and bound == set(TOOLS[role]))
            if role in {"query_controller", "update_controller"}:
                children = [saved["agents"][r] for r in (("retriever", "query_verifier") if role == "query_controller" else ("extractor", "update_verifier"))]
                check(role+"：实际子 Agent 与 Skill 绑定", set(info["sub_agent_id_list"]) == set(children) and saved["skill_id"] in {s["skill_id"] for s in info["skills"] if s.get("enabled")})
            native.append({"role": role, "agent_id": agent_id, "model_ids": [], "enabled": False, "published_versions": 0,
                           "tools": sorted(bound), "child_ids": info["sub_agent_id_list"]})
    finally: api.close()
    with httpx.Client(trust_env=False, timeout=10, headers={"Authorization": "Bearer "+access["mcp_token"], "Accept": "application/json, text/event-stream"}) as client:
        sequence = []
        def rpc(method, params):
            r = client.post("http://127.0.0.1:8780/mcp/", json={"jsonrpc": "2.0", "id": len(sequence)+1, "method": method, "params": params})
            r.raise_for_status()
            result = r.json()["result"]
            if result.get("isError"): raise RuntimeError("MCP_TOOL_FAILED")
            return result
        def call(name, arguments):
            sequence.append(name)
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            return result.get("structuredContent") or json.loads(result["content"][0]["text"])
        listed = rpc("tools/list", {})
        check("真实 MCP 协议列工具与原生登记一致", {t["name"] for t in listed["tools"]} == registered)
        # Verify the actual Docker route, not just a saved URL or loopback endpoint.
        # Token is piped via stdin, never included in process arguments or output.
        docker_code = """import json,sys,urllib.request
secret=json.load(sys.stdin)
request=urllib.request.Request('http://host.docker.internal:8780/mcp/',data=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}).encode(),headers={'Authorization':'Bearer '+secret['token'],'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
 with opener.open(request,timeout=8) as response: result=json.load(response)
 print(json.dumps({'tools':sorted(t['name'] for t in result['result']['tools'])}))
except Exception as error: print(json.dumps({'error_type':type(error).__name__}))
"""
        route = subprocess.run(["docker", "exec", "-i", "nexent-config", "python", "-c", docker_code],
            input=json.dumps({"token": access["mcp_token"]}), capture_output=True, text=True, timeout=15)
        docker_route = json.loads(route.stdout) if route.returncode == 0 else {}
        check("Nexent 容器通过实际 Docker 主机地址读取工作台工具", set(docker_route.get("tools", [])) == registered)
        opened = call("govkb_open_query", {"facts": facts, "branch_id": "branch-adult-self"})
        key = opened["task_id"]
        for _ in range(3):
            call("govkb_retrieve_query", {"task_id": key})
            verified = call("govkb_verify_query", {"task_id": key})
            state = call("govkb_query_state", {"task_id": key})
            if state["status"] == "can_finalize": break
        answer = call("govkb_finalize_query", {"task_id": key})
        check("真实分阶段工具一次取齐已知依据并独立核验", answer["task_id"] == key and answer["retrieval_rounds"] == 1 and answer["snapshot_sha256"] and all(r["evidence"] for s in answer["stages"] for r in s["items"]))
        check("接线不解除已有冲突暂停", {r["requirement_id"] for r in answer["paused"]} == before_paused)
    out = ROOT/"evidence/agent-workflow"
    sources = [*sorted((ROOT/"src/gov_mcp/decision").glob("*.py")), ROOT/"src/gov_mcp/decision/workbench.html",
               ROOT/"config/decision/connections.json", ROOT/"scripts/provision_agent_workflow.py", Path(__file__).resolve()]
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "passed": sum(c["passed"] for c in checks), "total": len(checks),
              "scope": "actual_local_platform_and_program_MCP_tools_only", "checks": checks, "native_agents": native, "probe": probe,
              "mcp_tools": sorted(registered), "actual_mcp_sequence": sequence, "query_task_id": key,
              "docker_route_verified": True,
              "actual_model_inference_requests": 0, "jev_network_requests": 0, "native_agent_run_requests": 0,
              "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (out/"connections-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "native_drafts": len(native), "mcp_tools": len(registered), "inference_requests": 0}))


if __name__ == "__main__": main()

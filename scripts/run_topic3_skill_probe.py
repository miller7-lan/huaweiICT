"""Exercise Nexent Skill upload, new isolated Agent, execution and private export."""

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

from benchmark_native import run_case
from nexent_api import NexentAPI, ROOT, RUNTIME, nonthinking_params
from private_runtime import ensure_private_directory


def main():
    private = RUNTIME / "toolchain/skill-probe-20261013"
    ensure_private_directory(private)
    public = ROOT / "evidence/toolchain/skill-probe-20261013"
    public.mkdir(parents=True, exist_ok=True)
    saved = private / "state.json"
    local = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "scope": "Technical Skill lifecycle and MCP integration only",
              "checks": {}, "errors": []}
    api = NexentAPI()
    stage = "signin"
    try:
        accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
        api.login(accounts["developer"])
        def checkpoint():
            saved.write_text(json.dumps(local, indent=2), encoding="utf-8")
        skill_path = ROOT / "config/skills/ict-toolchain-probe/SKILL.md"
        if "skill_id" not in local:
            stage = "skill_upload"
            response = api.http.post(api.base + "/skills/upload", headers={"Authorization": "Bearer " + api.token},
                files={"file": ("SKILL.md", skill_path.read_bytes(), "text/markdown")}, data={"source": "custom"})
            response.raise_for_status()
            skill = response.json()
            local["skill_id"] = skill["skill_id"]
            checkpoint()
        report["checks"]["skill_uploaded"] = True
        # The pinned initial-upload decoder can misdetect Chinese UTF-8. Its
        # update endpoint decodes UTF-8 explicitly; verify the actual saved text.
        source_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        if local.get("skill_source_sha256") != source_hash:
            stage = "skill_utf8_update"
            response = api.http.put(api.base + "/skills/ict-toolchain-probe/upload", headers={"Authorization": "Bearer " + api.token},
                files={"file": ("SKILL.md", skill_path.read_bytes(), "text/markdown")})
            response.raise_for_status()
            local["skill_source_sha256"] = source_hash
            local["prior_version"] = local.pop("version", None)
            checkpoint()
        stage = "skill_content_verification"
        skill_info = api.call("GET", f"/skills/{local['skill_id']}")
        if "本地 Skill 与 MCP 联合演练" not in skill_info.get("content", ""):
            raise RuntimeError("Skill Chinese content was not preserved")
        report["checks"]["skill_utf8_content_verified"] = True
        name = "ict_toolchain_probe_20261013"
        if "agent_id" not in local:
            stage = "agent_import"
            original = json.loads((ROOT / "config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
            agent = dict(original["agent_info"]["1"])
            action = "<code>\nimport json\nguide = read_skill_md('ict-toolchain-probe')\nif 'ICT_TOPIC3_TECHNICAL_PROBE_V1' not in guide:\n    final_answer(json.dumps({'status': 'ERROR', 'message_code': 'SKILL_MARKER_MISSING'}))\nelse:\n    environment = get_environment_status()\n    if isinstance(environment, str):\n        environment = json.loads(environment)\n    final_answer(json.dumps(environment, ensure_ascii=False))\n</code>"
            agent.update(name=name, display_name="ICT工具链演练：Skill与MCP", managed_agents=[], is_main_agent=True,
                skill_names=["ict-toolchain-probe"], model_names=[state["active_model"]],
                description="Only load ict-toolchain-probe, verify its marker and call get_environment_status once.",
                duty_prompt="Load the Skill before the MCP call. Use this one complete action exactly:\n" + action,
                constraint_prompt="Technical empty-store exercise. Do not create business facts or pass tenant/user identity arguments. No other tools, agents, plans or loops. Stop after the actual result. Use complete Python code actions with final_answer.",
                few_shots_prompt=action)
            imported = api.call("POST", "/agent/import", {"agent_info": {"agent_id": 1, "agent_info": {"1": agent}, "mcp_info": original["mcp_info"]}, "force_import": False})
            local["agent_id"] = imported["agent_id_mapping"]["1"]
            checkpoint()
            api.call("POST", "/agent/update", {"agent_id": local["agent_id"], "requested_output_tokens": 1024,
                "model_params_override": {str(state["models"][state["active_model"]]): {"temperature": .1, "extra_params": nonthinking_params()}}})
        stage = "skill_binding"
        if not local.get("binding_verified"):
            api.call("POST", "/agent/update", {"agent_id": local["agent_id"], "enabled_skill_ids": [local["skill_id"]]})
            local["binding_verified"] = True
            local["prior_version"] = local.pop("version", None)
            checkpoint()
        report["checks"]["skill_explicitly_bound"] = True
        stage = "agent_publish"
        if "version" not in local:
            published = api.call("POST", f"/agent/{local['agent_id']}/publish", {"version_name": "technical-skill-probe-v1", "release_note": "Technical exercise only; no business data"})
            local["version"] = published["version_no"]
            checkpoint()
        report["checks"]["isolated_agent_published"] = True
        state["agents"][name] = local["agent_id"]
        state["versions"][name] = local["version"]
        stage = "native_skill_execution"
        def assess(events, audit, role, principal):
            errors = []
            tools = [event.get("tool_name") for event in events if event.get("type") == "tool"]
            if tools != ["read_skill_md", "get_environment_status"]:
                errors.append("SKILL_MCP_ORDER_MISMATCH")
            if len(audit) != 1 or audit[0].get("tool") != "get_environment_status":
                errors.append("MCP_AUDIT_COUNT_MISMATCH")
            if any(row.get("principal_sha256") != principal for row in audit):
                errors.append("TRUSTED_IDENTITY_MISMATCH")
            if any(event.get("type") in ("error", "max_steps_reached") for event in events):
                errors.append("RUNTIME_ERROR")
            finals = [event.get("content") for event in events if event.get("type") == "final_answer" and not event.get("depth", 0)]
            result = None
            try:
                result = finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else json.loads("".join(finals))
                if not audit or result != audit[-1]["result"] or result.get("message_code") != "ENVIRONMENT_READY":
                    errors.append("FINAL_RESULT_MISMATCH")
            except (ValueError, TypeError, KeyError):
                errors.append("FINAL_RESULT_INVALID")
            return {"passed": not errors, "stop_reasons": errors or ["SKILL_AND_MCP_VERIFIED"], "result": result}
        record = run_case(api, state, "skill-probe", state["active_model"], 1, role_override="developer", agent_name=name,
            query_override="请按 ict-toolchain-probe 技能执行一次本地空白技术自检。", assessor=assess)
        (private / "native-raw.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        report["execution"] = {key: record[key] for key in ("conversation_id", "duration_ms", "passed", "stop_reasons", "result", "business_counts_after")}
        report["checks"]["native_skill_then_mcp"] = record["passed"]
        report["actual_tool_order"] = [event.get("tool_name") for event in record["events"] if event.get("type") == "tool"]
        stage = "private_agent_export"
        response = api.http.post(api.base + "/agent/export", headers={"Authorization": "Bearer " + api.token}, json={"agent_id": local["agent_id"]})
        response.raise_for_status()
        export = private / ("agent-export.zip" if "zip" in response.headers.get("content-type", "") else "agent-export.json")
        export.write_bytes(response.content)
        if "zip" not in response.headers.get("content-type", ""):
            raise RuntimeError("Bound Skill was not packaged in Agent export")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Invalid export archive")
            verified_skill = False
            for name in archive.namelist():
                if name.startswith("skills/") and name.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                        if inner.testzip() is not None:
                            raise RuntimeError("Invalid nested Skill archive")
                        verified_skill = verified_skill or any(b"ICT_TOPIC3_TECHNICAL_PROBE_V1" in inner.read(item) for item in inner.namelist() if item.endswith("SKILL.md"))
            if not verified_skill:
                raise RuntimeError("Export is missing the actual Skill content")
            report["export_archive_members"] = archive.namelist()
        report["checks"]["export_contains_skill_files"] = True
        report["export"] = {"content_type": response.headers.get("content-type"), "bytes": len(response.content),
                             "sha256": hashlib.sha256(response.content).hexdigest(), "storage": "private runtime; review connection settings before sharing"}
        report["checks"]["export_saved_privately"] = True
    except Exception as error:
        report["errors"].append({"stage": stage, "type": type(error).__name__})
    finally:
        api.close()
        report["agent_id"] = local.get("agent_id")
        report["skill_id"] = local.get("skill_id")
        report["passed"] = all(report["checks"].get(key) is True for key in ("skill_uploaded", "skill_utf8_content_verified", "skill_explicitly_bound", "isolated_agent_published", "native_skill_then_mcp", "export_saved_privately", "export_contains_skill_files")) and not report["errors"]
        path = public / "summary.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

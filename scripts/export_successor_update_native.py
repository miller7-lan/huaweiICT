"""Export the disabled Henan successor-update tree (controller, extractor, reviewer) used by the 10-21 A6 run.

The live drafts are first compared with the A6 draft ledger and Skill digest, then
exported unmodified through /agent/export. Read-only: no model call, no publication.
"""
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_competition_package import inspect_content, safe_name, sha  # noqa: E402
from nexent_api import NexentAPI  # noqa: E402
from provision_crossdoc_native_preview import read, write  # noqa: E402

LEDGER = ROOT / ".runtime/agent-autonomy-repair-20261020-a6/native-drafts.json"
SKILL = "govkb-successor-update"
SERVICE = "govkb_successor_update"
NAMES = {"controller": "govsuccessor_controller", "extractor": "govsuccessor_extractor",
         "reviewer": "govsuccessor_reviewer"}
ROLE_TOOLS = {"controller": {"govkb_successor_state", "govkb_successor_finish"},
              "extractor": {"govkb_successor_get", "govkb_successor_submit"},
              "reviewer": {"govkb_successor_get", "govkb_successor_review"}}
FIELDS = ("name", "display_name", "description", "enabled", "model_ids", "is_main_agent", "duty_prompt",
          "constraint_prompt", "few_shots_prompt", "max_steps", "requested_output_tokens",
          "enable_protocol_repair_retry", "allow_chat_metadata", "provide_run_summary")
PUBLIC = ROOT / "config/exports/successor-update-native-20261105/agent.json"
ARCHIVE = ROOT / "artifacts/competition-20261105/nexent-successor-update-native-export.zip"
OUTPUT = ROOT / "evidence/agent-autonomy-repair-20261020-a6/actual-export-20261105.json"


def check_role(api, role, identifier, saved):
    info = api.call("POST", "/agent/search_info", {"agent_id": identifier, "version_no": 0})
    if not isinstance(info, dict) or info.get("agent_id") != identifier or info.get("name") != NAMES[role]:
        raise RuntimeError("EXACT_RECORDED_AGENT_REQUIRED")
    if api.call("GET", f"/agent/{identifier}/versions").get("total") != 0:
        raise RuntimeError("REFUSE_PUBLISHED_AGENT")
    body = saved["draft_bodies"][role]
    tools, skills, children = info.get("tools", []), info.get("skills", []), info.get("sub_agent_id_list", [])
    differing = [key for key in FIELDS if key in body and info.get(key) != body[key]]
    if (differing or info.get("enabled") not in (False, None)
            or {t.get("tool_id") for t in tools} != set(body["enabled_tool_ids"])
            or {t.get("name") for t in tools} != ROLE_TOOLS[role]
            or any(t.get("source") != "mcp" or t.get("usage") != SERVICE for t in tools)
            or {s.get("skill_id") for s in skills} != set(body["enabled_skill_ids"])
            or set(children) != set(body["related_agent_ids"])):
        raise RuntimeError("LIVE_DRAFT_DIFFERS_FROM_A6_LEDGER: " + role + " " + ",".join(differing))
    return {"agent_id": identifier, "name": NAMES[role], "max_steps": info.get("max_steps"),
            "tools": sorted(t["name"] for t in tools), "skill_ids": sorted(s["skill_id"] for s in skills),
            "children": sorted(children)}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    saved = read(LEDGER)
    source = ROOT / "config/skills" / SKILL / "SKILL.md"
    if sha(source.read_bytes()) != saved["skill_sha256"]:
        raise RuntimeError("SKILL_SOURCE_DIFFERS_FROM_A6")
    api = NexentAPI()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        roles = {role: check_role(api, role, identifier, saved) for role, identifier in saved["agents"].items()}
        skill = api.call("GET", f"/skills/{saved['skill_id']}")
        if skill.get("name") != SKILL or skill.get("content", "").strip() != source.read_text("utf-8").split("---", 2)[2].strip():
            raise RuntimeError("LIVE_SKILL_DIFFERS_FROM_SOURCE")
        response = api.http.post(api.base + "/agent/export", json={"agent_id": saved["agents"]["controller"]},
                                 headers={"Authorization": "Bearer " + api.token})
        response.raise_for_status()
        raw = response.content
    finally:
        api.close()
    if len(raw) > 5 * 1024 ** 2:
        raise ValueError("BOUNDED_NATIVE_EXPORT_REQUIRED")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        if set(bundle.namelist()) != {"agent.json", "skills/" + SKILL + ".zip"} or bundle.testzip() is not None:
            raise ValueError("EXACT_NATIVE_EXPORT_MEMBERS_REQUIRED")
        agent_bytes = bundle.read("agent.json")
        inspect_content("agent.json", agent_bytes)
        exported = json.loads(agent_bytes)
        if (set(exported["agent_info"]) != {str(i) for i in saved["agents"].values()}
                or exported["agent_id"] != saved["agents"]["controller"]):
            raise ValueError("ACTUAL_EXPORTED_TREE_IDS_DIFFER")
        with zipfile.ZipFile(io.BytesIO(bundle.read("skills/" + SKILL + ".zip"))) as packed:
            matched = 0
            for name in packed.namelist():
                safe_name(name)
                if name.endswith("/"):
                    continue
                content = packed.read(name)
                inspect_content(name, content)
                if name.endswith("SKILL.md"):
                    if content.decode("utf-8").replace("\r\n", "\n").strip() != source.read_text("utf-8").replace("\r\n", "\n").strip():
                        raise ValueError("ACTUAL_EXPORTED_SKILL_BYTES_DIFFER")
                    matched += 1
            if matched != 1:
                raise ValueError("EXACT_ONE_EXPORTED_SKILL_REQUIRED")
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.write_bytes(agent_bytes)
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_bytes(raw)
    report = {"status": "PASS_ACTUAL_DISABLED_TREE_EXPORT", "run": "agent-autonomy-repair-20261020-a6",
              "agents": roles, "skill_id": saved["skill_id"], "skill_sha256": saved["skill_sha256"],
              "live_drafts_match_a6_ledger": True, "export_sha256": sha(raw), "agent_json_sha256": sha(agent_bytes),
              "public_json": str(PUBLIC.relative_to(ROOT)).replace("\\", "/"),
              "archive": str(ARCHIVE.relative_to(ROOT)).replace("\\", "/"),
              "credentials_scan_passed": True, "mcp_service_started": False, "model_calls": 0, "published": False}
    write(OUTPUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

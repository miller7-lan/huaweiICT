"""Portable reference configuration from actual native exports; not an import API payload."""
from copy import deepcopy
import io
import json
from pathlib import Path
import zipfile

from gov_mcp.matter_graph import canonical_hash, file_hash
from gov_mcp.matter_native import native_profile

ROLE_TOOLS = {"controller": {"govmatter_open", "govmatter_state", "govmatter_finalize"},
    "retriever": {"govmatter_retrieve"}, "verifier": {"govmatter_verify"}}
PARAMS = {"temperature": .1, "extra_params": {"__custom__": {"reasoning_effort": "none"}}}
PROMPTS = ("duty_prompt", "constraint_prompt", "few_shots_prompt")


def portable_spec(export):
    profile = native_profile("dataflow-v4")
    rows = export.get("agent_info")
    if not isinstance(rows, dict) or len(rows) != 3: raise ValueError("THREE_NATIVE_DRAFTS_REQUIRED")
    by_name = {row["name"]: row for row in rows.values()}
    if set(by_name) != set(profile["names"].values()): raise ValueError("EXACT_NATIVE_NAMES_REQUIRED")
    actual = {role: by_name[name] for role, name in profile["names"].items()}
    if export.get("agent_id") != actual["controller"]["agent_id"]: raise ValueError("ROOT_CONTROLLER_REQUIRED")
    for role, row in actual.items():
        expected_children = {actual[r]["agent_id"] for r in ("retriever", "verifier")} if role == "controller" else set()
        if (row.get("enabled") is not False or row.get("is_main_agent") is not (role == "controller")
            or set(row.get("managed_agents", [])) != expected_children):
            raise ValueError("NATIVE_ROLE_OR_GRAPH_MISMATCH")
        if row.get("max_steps") != (profile["main_steps"] if role == "controller" else 2):
            raise ValueError("NATIVE_STEP_LIMIT_MISMATCH")
        if row.get("requested_output_tokens") != profile["main_output_tokens" if role == "controller" else "child_output_tokens"]:
            raise ValueError("NATIVE_OUTPUT_LIMIT_MISMATCH")
        if any(row.get(k) is not False for k in ("provide_run_summary", "allow_chat_metadata", "enable_protocol_repair_retry")):
            raise ValueError("NATIVE_EXTRA_CAPABILITY_MISMATCH")
        if any(row.get(k) not in (None, {}, "", []) for k in ("verification_config", "context_policy", "greeting_message", "example_questions", "business_logic_model_id", "business_logic_model_name")):
            raise ValueError("UNEXPECTED_RUNTIME_CONFIGURATION")
        if row.get("prompt_template_id") not in (None, 0) or row.get("prompt_template_name") not in (None, "system_default"):
            raise ValueError("CUSTOM_PROMPT_TEMPLATE_NOT_PORTABLE")
        tools = row.get("tools", [])
        if ({t.get("name") for t in tools} != ROLE_TOOLS[role] or len(tools) != len(ROLE_TOOLS[role])
            or any(t.get("class_name") != t.get("name") or t.get("source") != "mcp"
                or t.get("usage") != profile["service"] or t.get("params") != {} for t in tools)):
            raise ValueError("EXACT_TOOL_SERVICE_BINDING_REQUIRED")
        expected_model = "gov-qwen3-8b:ctx8192-v1" if role == "controller" else "gov-qwen3-4b:ctx8192-v1"
        ids = row.get("model_ids")
        if row.get("model_names") != [expected_model] or not isinstance(ids, list) or len(ids) != 1:
            raise ValueError("REGISTERED_QWEN_BINDING_REQUIRED")
        if row.get("model_params_override") != {str(ids[0]): PARAMS}: raise ValueError("MODEL_PARAMETERS_CHANGED")
        if row.get("skill_names") != ([profile["skill"]] if role == "controller" else []): raise ValueError("EXACT_SKILL_BINDING_REQUIRED")
        if any(not isinstance(row.get(k), str) or not row[k] for k in PROMPTS): raise ValueError("FULL_PROMPTS_REQUIRED")
    if export.get("mcp_info") != [{"mcp_server_name": profile["service"], "mcp_url": "http://host.docker.internal:8789/sse"}]:
        raise ValueError("EXPECTED_LOOPBACK_SERVICE_REQUIRED")
    roles = {}
    for role, row in actual.items():
        roles[role] = {"name": row["name"], "enabled": False, "is_main_agent": role == "controller",
            "model_display_name": row["model_names"][0], "inference_parameters": deepcopy(PARAMS),
            "max_steps": row["max_steps"], "requested_output_tokens": row["requested_output_tokens"],
            "provide_run_summary": False, "allow_chat_metadata": False, "enable_protocol_repair_retry": False,
            "managed_roles": ["retriever", "verifier"] if role == "controller" else [],
            "tools": [{"name": name, "source": "mcp", "usage": profile["service"], "params": {}} for name in sorted(ROLE_TOOLS[role])],
            "skill_names": row["skill_names"], "prompts": {k: row[k] for k in PROMPTS}}
    return {"schema": 1, "kind": "native_draft_restore_reference_not_direct_import_payload", "protocol": "dataflow-v4",
        "roles": roles, "mcp": {"service": profile["service"], "target_port": 8789, "authorization_required": True},
        "native_run_limit": 1, "run_seconds_limit": 1200, "formal_publication": False,
        "target_import_or_inference_performed": False, "native_import_api_payload": False}


def unpack_export(raw, expected_skill):
    if len(raw) > 8 * 1024**2: raise ValueError("BOUNDED_EXPORT_REQUIRED")
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        if len(outer.infolist()) != 2 or sum(i.file_size for i in outer.infolist()) > 1024**2:
            raise ValueError("BOUNDED_EXPORT_MEMBERS_REQUIRED")
        if outer.testzip() or set(outer.namelist()) != {"agent.json", "skills/govmatter-source-proof-dataflow.zip"}:
            raise ValueError("EXACT_NATIVE_EXPORT_MEMBERS_REQUIRED")
        value = json.loads(outer.read("agent.json")); spec = portable_spec(value)
        with zipfile.ZipFile(io.BytesIO(outer.read("skills/govmatter-source-proof-dataflow.zip"))) as inner:
            if len(inner.infolist()) > 3 or sum(i.file_size for i in inner.infolist()) > 128 * 1024:
                raise ValueError("BOUNDED_SKILL_REQUIRED")
            names = [n for n in inner.namelist() if not n.endswith("/")]
            if len(names) != 1 or not names[0].endswith("SKILL.md") or inner.testzip(): raise ValueError("EXACT_SKILL_FILE_REQUIRED")
            skill = inner.read(names[0])
    if skill.decode("utf-8-sig").replace("\r\n", "\n").strip() != expected_skill.decode("utf-8-sig").replace("\r\n", "\n").strip():
        raise ValueError("ACTUAL_SKILL_SOURCE_MISMATCH")
    return spec, skill


def verify_bundle(root):
    root = Path(root).resolve(); manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected_paths = {"restore-spec.json", "skills/govmatter-source-proof-dataflow/SKILL.md", "README.md"}
    if len(manifest["files"]) != 3 or {row["path"] for row in manifest["files"]} != expected_paths: raise ValueError("EXACT_ASSET_FILES_REQUIRED")
    for row in manifest["files"]:
        target = (root / row["path"]).resolve()
        if not target.is_relative_to(root) or file_hash(target.read_bytes()) != row["sha256"]: raise ValueError("ASSET_FILE_BINDING_FAILED")
    if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != expected_paths | {"manifest.json"}:
        raise ValueError("UNLISTED_ASSET_FILE")
    spec = json.loads((root / "restore-spec.json").read_text(encoding="utf-8"))
    if canonical_hash(spec) != manifest["restore_spec_sha256"] or spec.get("native_import_api_payload") is not False:
        raise ValueError("RESTORE_REFERENCE_BINDING_FAILED")
    return spec

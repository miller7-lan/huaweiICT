"""Synthetic export contracts; not native inference or cold-platform acceptance."""
from copy import deepcopy
import io
import json
from pathlib import Path
import zipfile

import pytest

from gov_mcp.matter_agent_assets import PARAMS, ROLE_TOOLS, portable_spec, unpack_export, verify_bundle
from gov_mcp.matter_graph import canonical_hash, file_hash
from gov_mcp.matter_native import native_profile


def export_fixture():
    profile = native_profile("dataflow-v4"); ids = {"controller": 901, "retriever": 902, "verifier": 903}
    rows = {}
    for role, agent_id in ids.items():
        rows[str(agent_id)] = {"agent_id": agent_id, "tenant_id": "fixture-private-tenant", "name": profile["names"][role],
            "enabled": False, "is_main_agent": role == "controller", "managed_agents": [902, 903] if role == "controller" else [],
            "max_steps": 2, "requested_output_tokens": 512 if role == "controller" else 256,
            "provide_run_summary": False, "allow_chat_metadata": False, "enable_protocol_repair_retry": False,
            "tools": [{"name": n, "class_name": n, "source": "mcp", "usage": profile["service"], "params": {}} for n in ROLE_TOOLS[role]],
            "model_ids": [77], "model_names": ["gov-qwen3-8b:ctx8192-v1" if role == "controller" else "gov-qwen3-4b:ctx8192-v1"],
            "model_params_override": {"77": deepcopy(PARAMS)}, "skill_names": [profile["skill"]] if role == "controller" else [],
            **{k: "Explicit contract fixture prompt " + k for k in ("duty_prompt", "constraint_prompt", "few_shots_prompt")}}
    return {"agent_id": 901, "agent_info": rows, "mcp_info": [{"mcp_server_name": profile["service"], "mcp_url": "http://host.docker.internal:8789/sse"}]}


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for name, data in files.items(): z.writestr(name, data)
    return output.getvalue()


def test_reference_omits_private_ids_without_changing_input():
    value = export_fixture(); before = deepcopy(value); spec = portable_spec(value)
    text = json.dumps(spec)
    for private in ("fixture-private-tenant", "901", "902", "903", "model_ids", "agent_id", "tenant_id"):
        assert private not in text
    assert value == before and not spec["native_import_api_payload"]
    assert spec["roles"]["controller"]["managed_roles"] == ["retriever", "verifier"]


@pytest.mark.parametrize(("role", "key", "value", "error"), [
    ("902", "enabled", True, "NATIVE_ROLE_OR_GRAPH_MISMATCH"),
    ("903", "managed_agents", [902], "NATIVE_ROLE_OR_GRAPH_MISMATCH"),
    ("901", "model_params_override", {"78": PARAMS}, "MODEL_PARAMETERS_CHANGED"),
    ("902", "model_names", ["wrong-model"], "REGISTERED_QWEN_BINDING_REQUIRED"),
    ("903", "prompt_template_id", 12, "CUSTOM_PROMPT_TEMPLATE_NOT_PORTABLE"),
])
def test_unsafe_cross_platform_configuration_is_rejected(role, key, value, error):
    fixture = export_fixture(); fixture["agent_info"][role][key] = value
    with pytest.raises(ValueError, match=error): portable_spec(fixture)


def test_same_tool_name_on_another_service_cannot_pass():
    fixture = export_fixture(); fixture["agent_info"]["902"]["tools"][0]["usage"] = "legacy-service"
    with pytest.raises(ValueError, match="EXACT_TOOL_SERVICE_BINDING_REQUIRED"): portable_spec(fixture)


def test_exported_skill_must_match_actual_source_and_members_are_restricted():
    skill = b"# Explicit fixture skill\n"
    raw = zip_bytes({"agent.json": json.dumps(export_fixture()), "skills/govmatter-source-proof-dataflow.zip": zip_bytes({"name/SKILL.md": skill})})
    spec, result = unpack_export(raw, skill)
    assert result == skill and spec["protocol"] == "dataflow-v4"
    with pytest.raises(ValueError, match="ACTUAL_SKILL_SOURCE_MISMATCH"): unpack_export(raw, b"different skill")
    with pytest.raises(ValueError, match="EXACT_NATIVE_EXPORT_MEMBERS_REQUIRED"):
        unpack_export(zip_bytes({"../bad": b"bad", "agent.json": b"{}"}), skill)


def fixture_bundle(root):
    spec = portable_spec(export_fixture())
    files = {"restore-spec.json": json.dumps(spec).encode(), "skills/govmatter-source-proof-dataflow/SKILL.md": b"# Fixture", "README.md": b"fixture"}
    for name, data in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    (root / "manifest.json").write_text(json.dumps({"restore_spec_sha256": canonical_hash(spec), "files": [{"path": name, "sha256": file_hash(data)} for name, data in files.items()]}))


def test_tamper_and_unlisted_files_fail_without_execution(tmp_path):
    fixture_bundle(tmp_path); assert not verify_bundle(tmp_path)["target_import_or_inference_performed"]
    (tmp_path / "README.md").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="ASSET_FILE_BINDING_FAILED"): verify_bundle(tmp_path)
    fixture_bundle(tmp_path); (tmp_path / "hidden-token.txt").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="UNLISTED_ASSET_FILE"): verify_bundle(tmp_path)

"""Henan native draft configuration stays separate from historical profiles."""
from pathlib import Path

import pytest

import provision_henan_multimodal_native as henan
import provision_ontology_crossdoc_native as original


ROOT = Path(__file__).resolve().parents[1]


def saved(provisioner):
    return {"model_id": 7, "skill_id": 11, "agents": {"controller": 101, "retriever": 102, "verifier": 103},
            "tool_ids": {name: index for index, name in enumerate(sorted(provisioner.TOOLS), 1)}}


def test_profile_paths_and_globals_do_not_change_the_existing_module(tmp_path):
    before = {key: getattr(original, key) for key in
              ("RUNTIME", "OUTPUT", "SCENARIO", "SKILL", "NAMES", "DISPLAY_PREFIX", "PROTOCOL", "first_action")}
    profile = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "evidence")
    assert profile is not original
    assert profile.RUNTIME == tmp_path / "runtime" and profile.OUTPUT == tmp_path / "evidence"
    assert profile.SCENARIO == "henan_expiry_renewal_archive"
    assert profile.SKILL == "govkb-henan-multimodal-preview"
    assert profile.PORT == original.PORT == 8785
    assert profile.SERVICE == original.SERVICE and profile.TOOLS == original.TOOLS
    assert all(getattr(original, key) == value for key, value in before.items())
    assert original.DISPLAY_PREFIX == "已复核本体跨文档预览 "
    assert "nanjing_outside_old_card" in original.first_action()
    assert not profile.RUNTIME.exists() and not profile.OUTPUT.exists()
    second = henan.profile_provisioner(tmp_path / "other-runtime", tmp_path / "other-evidence")
    second.NAMES["controller"] = "test-only"
    assert profile.NAMES == henan.NAMES


def test_first_action_and_protocol_target_only_the_henan_scenario(tmp_path):
    profile = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "evidence")
    action = profile.first_action()
    assert profile.SKILL in action and "scenario_id='henan_expiry_renewal_archive'" in action
    assert "govhenanmultimodal_retriever(task=" in profile.PROTOCOL
    assert "govhenanmultimodal_verifier(task=" in profile.PROTOCOL
    assert "four-node" in profile.PROTOCOL and "HTML, XLSX and PDF" in profile.PROTOCOL
    assert "17 located segments" in profile.PROTOCOL
    assert "JSON STRINGS" in profile.PROTOCOL and "final_answer(result)" in profile.PROTOCOL
    assert "existing attributed AI visual transcription" in profile.PROTOCOL
    assert "day_unspecified" in profile.PROTOCOL
    for forbidden in ("Nanjing", "nanjing", "govontologycrossdoc_", "govkb-ontology-crossdoc-preview"):
        assert forbidden not in profile.PROTOCOL + action
    with pytest.raises(ValueError, match="FIXED_SCENARIO_REQUIRED"):
        profile.first_action("nanjing_outside_old_card")


@pytest.mark.parametrize("role", ["controller", "retriever", "verifier"])
def test_disabled_unpublished_drafts_keep_role_permissions_and_step_bounds(tmp_path, role):
    profile = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "evidence")
    state = saved(profile)
    body = profile.draft_body(role, state, 2)
    assert body["name"] == "govhenanmultimodal_" + role
    assert body["display_name"] == "河南三模态归档预览 " + role
    assert body["enabled"] is False and body["ingroup_permission"] == "READ_ONLY"
    assert body["model_ids"] == [7] and body["requested_output_tokens"] == 1024
    assert body["max_steps"] == (6 if role == "controller" else 3)
    assert body["enable_protocol_repair_retry"] is True
    assert body["related_agent_ids"] == ([102, 103] if role == "controller" else [])
    assert body["enabled_skill_ids"] == ([11] if role == "controller" else [])
    assert body["enabled_tool_ids"] == [state["tool_ids"][name] for name in profile.ROLE_TOOLS[role]]
    assert all(word not in str(body) for word in ("Nanjing", "nanjing", "govontologycrossdoc_"))


def test_name_collision_check_uses_the_same_henan_display_names(tmp_path):
    profile = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "evidence")

    class NameCheckAPI:
        def call(self, method, path, body):
            assert method == "POST" and path == "/agent/check_name"
            assert body["items"] == [{"name": profile.NAMES[role],
                                      "display_name": profile.DISPLAY_PREFIX + role}
                                     for role in profile.NAMES]
            return [{"name_conflict": False, "display_name_conflict": False, "conflict_agents": []}
                    for _ in body["items"]]

    profile.check_agent_names(NameCheckAPI(), {})


def test_skill_preserves_archive_limits_and_shared_fingerprint_checks(tmp_path):
    from gov_mcp.henan_multimodal_preview import code_fingerprints

    profile = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "evidence")
    assert profile.code_fingerprints is code_fingerprints
    skill = (ROOT / "config/skills" / profile.SKILL / "SKILL.md").read_text("utf-8")
    assert "name: " + profile.SKILL in skill and profile.SCENARIO in skill
    assert "17 locators" in skill and "four-node" in skill
    assert "not new image OCR" in skill and "completed human review" in skill
    assert "day_unspecified" in skill and "PAUSED_UNREVIEWED" in skill
    assert "actual_fee_yuan=null" in skill and "promised_duration=null" in skill
    assert all(word not in skill for word in ("Nanjing", "nanjing", "govontologycrossdoc_"))

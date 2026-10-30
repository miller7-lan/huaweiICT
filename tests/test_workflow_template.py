"""The workflow template reproduces validated profiles and guards new ones."""
import copy
import json
from pathlib import Path

import pytest

import provision_henan_multimodal_native as henan
import provision_ontology_crossdoc_native as nanjing
import workflow_template as template


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config/skill-templates/profiles"


def profile(name):
    return template.load_profile(PROFILES / (name + ".json"))


def test_henan_profile_reproduces_the_natively_accepted_skill_and_protocol():
    henan_profile = profile("henan-multimodal")
    accepted = (ROOT / "config/skills/govkb-henan-multimodal-preview/SKILL.md").read_text("utf-8")
    assert template.render_skill(henan_profile) == accepted
    assert template.render_protocol(henan_profile) == henan.PROTOCOL


def test_nanjing_protocol_matches_and_legacy_skill_is_never_overwritten(tmp_path):
    nanjing_profile = profile("nanjing-ontology-crossdoc")
    assert template.render_protocol(nanjing_profile) == nanjing.PROTOCOL
    path, status = template.skill_status(nanjing_profile)
    assert status == "differs"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="SKILL_EXISTS_WITH_DIFFERENT_CONTENT"):
        template.write_skill(nanjing_profile)
    assert path.read_bytes() == before


def test_factory_matches_the_henan_profile_loader_without_touching_the_shared_module(tmp_path):
    keys = ("RUNTIME", "OUTPUT", "SCENARIO", "SKILL", "NAMES", "DISPLAY_PREFIX", "PROTOCOL", "first_action")
    before = {key: getattr(nanjing, key) for key in keys}
    built = template.profile_provisioner(profile("henan-multimodal"), tmp_path / "runtime", tmp_path / "out")
    reference = henan.profile_provisioner(tmp_path / "runtime", tmp_path / "out")
    for key in ("RUNTIME", "OUTPUT", "SCENARIO", "SKILL", "NAMES", "DISPLAY_PREFIX", "PROTOCOL",
                "PORT", "SERVICE", "TOOLS", "ROLE_TOOLS"):
        assert getattr(built, key) == getattr(reference, key)
    assert built.first_action() == reference.first_action()
    assert built.code_fingerprints is reference.code_fingerprints
    assert all(getattr(nanjing, key) == value for key, value in before.items())


def test_new_profile_renders_into_its_own_skill_directory(tmp_path):
    new = copy.deepcopy(profile("henan-multimodal"))
    new.update(profile="beijing-renewal-v1", skill_name="govkb-beijing-renewal-preview",
               scenario_id="beijing_renewal_archive", title="北京换领归档预览",
               agents={"controller": "govbeijing_controller", "retriever": "govbeijing_retriever",
                       "verifier": "govbeijing_verifier"})
    path, result = template.write_skill(new, tmp_path)
    assert result == "written" and path == tmp_path / "govkb-beijing-renewal-preview/SKILL.md"
    text = path.read_text("utf-8")
    assert "`beijing_renewal_archive`" in text and "`govbeijing_verifier` child" in text
    assert "{{" not in text and "henan_expiry_renewal_archive" not in text
    assert template.write_skill(new, tmp_path)[1] == "unchanged"
    built = template.profile_provisioner(new, tmp_path, tmp_path)
    assert "scenario_id='beijing_renewal_archive'" in built.first_action()
    assert "govbeijing_retriever(task=" in built.PROTOCOL


def test_example_profile_is_rejected_until_its_wording_is_filled():
    raw = json.loads((PROFILES / "example-new-profile.json").read_text("utf-8"))
    with pytest.raises(ValueError, match="UNFILLED_PROFILE_SLOT"):
        template.validate(raw)


@pytest.mark.parametrize("change, error", [
    (lambda p: p.update(extra=1), "INVALID_PROFILE_KEYS"),
    (lambda p: p.update(skill_name="../escape"), "INVALID_PROFILE_FIELD: skill_name"),
    (lambda p: p.update(fingerprints="os:system"), "INVALID_PROFILE_FIELD: fingerprints"),
    (lambda p: p["agents"].update(verifier=p["agents"]["retriever"]), "INVALID_PROFILE_AGENTS"),
    (lambda p: p["agents"].update(retriever="Bad Name"), "INVALID_PROFILE_AGENTS"),
    (lambda p: p["skill"].pop("evidence_notes"), "INVALID_PROFILE_SKILL_SLOTS"),
    (lambda p: p["skill"].update(admission=["missing punctuation"]), "INVALID_PROFILE_SLOT: admission"),
    (lambda p: p["skill"].update(verify_detail=["two\nlines"]), "INVALID_PROFILE_SLOT: verify_detail"),
    (lambda p: p.update(protocol_notes=["{{skill_name}}"]), "UNFILLED_PROFILE_SLOT: protocol_notes"),
])
def test_invalid_profiles_stop_before_rendering(change, error):
    bad = copy.deepcopy(profile("henan-multimodal"))
    change(bad)
    with pytest.raises(ValueError, match=error):
        template.render_skill(bad)

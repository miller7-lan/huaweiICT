import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from provision_m2 import bundle


def test_bundle_has_independent_m2_names_and_injected_identity_tools():
    value = bundle()
    names = {a["name"] for a in value["agent_info"].values()}
    assert names == {"govm2_coordinator", "govm2_citizen_assistant", "govm2_staff_assistant"}
    for agent in value["agent_info"].values():
        assert agent["display_name"].startswith("M2")
        assert all(t["usage"] == "govm2_preview_mcp" for t in agent["tools"])
        assert "govx_" not in agent["duty_prompt"]
        assert agent["requested_output_tokens"] == 1024

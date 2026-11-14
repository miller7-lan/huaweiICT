"""Wiring of the flow's draft assistant reuses the existing scripts without changing them."""
import frozen_flow_native as native
import provision_frozen_qa_native as original
import run_frozen_qa_native as original_run


def test_private_copy_carries_the_flow_settings_and_leaves_the_original_alone():
    before = (original.SERVICE, original.PORT, set(original.TOOLS), original.NAME, original.PROMPT, original.RUNTIME)
    module = native.provisioner()
    assert module is not original and (module.SERVICE, module.PORT, module.NAME) == ("govkb_frozen_flow", 8787, native.NAME)
    draft = module.body(7, {"govkb_flow_open": 11, "govkb_flow_submit": 12}, 3)
    assert draft["enabled"] is False and draft["max_steps"] == native.MAX_STEPS and draft["name"] == native.NAME
    assert draft["enabled_tool_ids"] == [11, 12] and draft["duty_prompt"] == native.PROMPT
    assert draft["related_agent_ids"] == [] and draft["enabled_skill_ids"] == []
    assert (original.SERVICE, original.PORT, set(original.TOOLS), original.NAME, original.PROMPT, original.RUNTIME) == before
    assert original.body(7, {"govkb_frozen_retrieve": 1, "govkb_frozen_verify": 2}, 3)["max_steps"] == 6


def test_runner_uses_the_flow_runtime_only():
    module = native.runner()
    assert module.RUNTIME == native.RUNTIME and module.OUTPUT == native.OUTPUT
    assert original_run.RUNTIME != native.RUNTIME


def test_prompt_names_both_tools_and_the_plan_registers_nothing():
    assert all(tool in native.PROMPT for tool in native.TOOLS)
    assert "Never call govkb_flow_open twice" in native.PROMPT
    plan = native.plan()
    assert plan["enabled"] is False and plan["published"] is False and plan["tools"] == sorted(native.TOOLS)

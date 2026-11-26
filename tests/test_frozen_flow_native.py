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
    # The flow gets more model starts; the earlier drafts keep their own limit.
    assert module.MAX_MODEL_STARTS == native.MAX_MODEL_STARTS == 10 and original_run.MAX_MODEL_STARTS == 8


def test_a_native_run_is_refused_when_the_service_uses_another_prompt_profile():
    import pytest
    health = {"service": "govkb_frozen_flow", "status": "UP", "rules": "light", "corpus_sha256": "c" * 64}
    assert native.check_service_rules(health, "light")["rules"] == "light"
    with pytest.raises(RuntimeError, match="SERVICE_RULES_MISMATCH"):
        native.check_service_rules(health, "strict")
    with pytest.raises(RuntimeError, match="FLOW_SERVICE_NOT_RUNNING"):
        native.check_service_rules({"service": "govkb_frozen_qa", "status": "UP", "rules": "strict"}, "strict")


def test_prompt_names_both_tools_and_the_plan_registers_nothing():
    assert all(tool in native.PROMPT for tool in native.TOOLS)
    assert "Never call govkb_flow_open twice" in native.PROMPT
    plan = native.plan()
    assert plan["enabled"] is False and plan["published"] is False and plan["tools"] == sorted(native.TOOLS)


def test_open_ok_without_submit_is_not_a_pass():
    """2026-11-26 X16 on kimi: open returned OK, the model never submitted, and the platform
    promoted its plain reasoning to the final answer. That skipped every check."""
    import frozen_flow_native as flow_native
    module = flow_native.runner()
    opened = {"type": "execution_logs", "content": '{\n  "status": "OK",\n  "task_id": "T"}'}
    skipped = [{"type": "tool", "tool_name": "govkb_flow_open"}, opened,
               {"type": "final_answer", "content": "思考：现提交逐条引文供核验。"}]
    report = module.judge({"transport_completed": True, **module.summarize(skipped), "model_starts": 4})
    assert report["first_status"] == "OK" and not report["passed"]
    assert report["failures"] == ["CHECK_SKIPPED_AFTER_OPEN_OK"]
    # X19: open, two submits, final_answer called as a tool.
    checked = [{"type": "tool", "tool_name": "govkb_flow_open"}, opened,
               {"type": "tool", "tool_name": "govkb_flow_submit"}, {"type": "tool", "tool_name": "govkb_flow_submit"},
               {"type": "tool", "tool_name": "final_answer"}, {"type": "final_answer", "content": "本题停止。"}]
    assert module.judge({"transport_completed": True, **module.summarize(checked), "model_starts": 5})["passed"]
    # A scope stop at open legitimately ends without a submit.
    stopped = [{"type": "tool", "tool_name": "govkb_flow_open"},
               {"type": "execution_logs", "content": '{"status": "STOP"}'},
               {"type": "final_answer", "content": "不在资料范围，停止。"}]
    assert module.judge({"transport_completed": True, **module.summarize(stopped), "model_starts": 2})["passed"]


def test_export_accepts_a_published_agent_only_when_allowed():
    import json
    import pytest
    import export_frozen_qa_native as exporter

    class Response:
        def __init__(self, enabled):
            body = {"data": {"agent_id": 1, "agent_info": {"1": {"name": native.NAME, "enabled": enabled, "tools": []}}}}
            self.content = json.dumps(body).encode()
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class Api:
        base, token = "http://x", "t"

        def __init__(self, enabled):
            self.http = type("H", (), {"post": lambda _s, *a, **k: Response(enabled)})()

    with pytest.raises(ValueError, match="DISABLED_DRAFT"):
        exporter.export(Api(True), 1, native.NAME)
    assert exporter.export(Api(True), 1, native.NAME, allow_enabled=True)[2]["enabled"] is True
    with pytest.raises(ValueError, match="RECORDED_AGENT"):
        exporter.export(Api(False), 1, "other_name", allow_enabled=True)

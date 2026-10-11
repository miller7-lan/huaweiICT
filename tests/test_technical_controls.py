"""Verify isolated account controls and executable Agent examples."""

import json
import re
from pathlib import Path

import pytest

from technical_controls import disable_technical_memory


@pytest.mark.parametrize("setting", [False, "false"])
def test_memory_controls_are_verified_against_native_response(setting):
    calls = []
    class API:
        def call(self, method, path, body=None):
            calls.append((method, path, body))
            return {"MEMORY_SWITCH": setting, "DREAMING_SWITCH": setting}
    disable_technical_memory(API())
    assert calls[:2] == [("POST", "/memory/config/set", {"key": key, "value": False})
                         for key in ("MEMORY_SWITCH", "DREAMING_SWITCH")]


@pytest.mark.parametrize("config", [{}, {"MEMORY_SWITCH": False, "DREAMING_SWITCH": True}])
def test_unconfirmed_memory_configuration_stops_sampling(config):
    class API:
        def call(self, *args):
            return config
    with pytest.raises(RuntimeError):
        disable_technical_memory(API())


@pytest.mark.parametrize("role,status", [("citizen", "OK"), ("staff", "OK"), ("citizen", "ERROR")])
def test_examples_preserve_actual_results_and_route_from_environment(role, status):
    bundle = json.loads(Path("config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
    agents = {a["name"]: a for a in bundle["agent_info"].values()}
    actual = {"status": "EMPTY" if role == "citizen" else "BLOCKED", "items": [],
              "citations": [], "knowledge_version": None, "message_code": "technical-test",
              "data": {"request_id": "actual-tool-id", "missing": ["case", "materials", "evidence"]}}
    environment = {"status": status, "data": {"role": role}}
    calls, answers = [], []
    def child(name, **kwargs):
        calls.append(name)
        tool = "search_evidence" if name == "citizen_assistant" else "check_case_prerequisites"
        child_answers = []
        def invoke():
            calls.append(tool)
            return json.dumps(actual)
        ns = {tool: invoke, "final_answer": child_answers.append}
        code = re.findall(r"<code>(.*?)</code>", agents[name]["few_shots_prompt"], re.S)
        assert len(code) == 1
        exec(code[0], ns)
        return child_answers[0]
    def get_environment():
        calls.append("get_environment_status")
        return json.dumps(environment)
    ns = {"get_environment_status": get_environment, "final_answer": answers.append,
          "citizen_assistant": lambda **kw: child("citizen_assistant", **kw),
          "staff_assistant": lambda **kw: child("staff_assistant", **kw)}
    for code in re.findall(r"<code>(.*?)</code>", agents["gov_coordinator"]["few_shots_prompt"], re.S):
        exec(code, ns)
    assert len(answers) == 1
    assert json.loads(answers[0]) == (actual if status == "OK" else environment)
    expected = ["get_environment_status"]
    if status == "OK":
        expected += [role + "_assistant", "search_evidence" if role == "citizen" else "check_case_prerequisites"]
    assert calls == expected

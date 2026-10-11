"""Verify isolated account controls and executable Agent examples."""

import ast
import json
import re
from pathlib import Path

import pytest

from technical_controls import disable_technical_memory


def test_memory_controls_are_verified_against_pinned_native_service():
    calls = []
    stored = {}
    service = Path("vendor/nexent/backend/services/memory_config_service.py")
    functions = {"set_memory_switch", "set_dreaming_switch", "get_user_configs", "_aggregate_records"}
    tree = ast.parse(service.read_text(encoding="utf-8"))
    selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                              and node.name in functions], type_ignores=[])
    from typing import Dict, List, Union
    def update(user_id, key, value):
        assert user_id == "isolated-test-user"
        stored[key] = value
        return True
    ns = {"Dict": Dict, "List": List, "Union": Union, "_MULTI_TYPE": "multi",
          "MEMORY_SWITCH_KEY": "MEMORY_SWITCH", "DREAMING_SWITCH_KEY": "DREAMING_SWITCH",
          "MEMORY_AGENT_SHARE_KEY": "MEMORY_AGENT_SHARE", "DEFAULT_MEMORY_SWITCH_KEY": "Y",
          "DEFAULT_DREAMING_SWITCH_KEY": "Y", "DEFAULT_MEMORY_AGENT_SHARE_KEY": "always",
          "_update_single_config": update,
          "get_all_configs_by_user_id": lambda _: [
              {"config_key": key, "config_value": value, "value_type": "single"}
              for key, value in stored.items()]}
    exec(compile(selected, str(service), "exec"), ns)
    class API:
        def call(self, method, path, body=None):
            calls.append((method, path, body))
            if method == "POST":
                setter = "set_memory_switch" if body["key"] == "MEMORY_SWITCH" else "set_dreaming_switch"
                return {"success": ns[setter]("isolated-test-user", body["value"])}
            return ns["get_user_configs"]("isolated-test-user")
    disable_technical_memory(API())
    assert calls[:2] == [("POST", "/memory/config/set", {"key": key, "value": False})
                         for key in ("MEMORY_SWITCH", "DREAMING_SWITCH")]


@pytest.mark.parametrize("config", [{}, {"MEMORY_SWITCH": "N", "DREAMING_SWITCH": "Y"},
                                   {"MEMORY_SWITCH": "Y", "DREAMING_SWITCH": "N"}])
def test_unconfirmed_memory_configuration_stops_sampling(config):
    class API:
        def call(self, *args):
            return config
    with pytest.raises(RuntimeError):
        disable_technical_memory(API())


def test_preflight_failure_records_stage_without_exception_secrets(tmp_path, monkeypatch, capsys):
    import benchmark_native
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    (runtime / "nexent-state.json").write_text("{}")
    (runtime / "accounts.json").write_text(json.dumps({"tenant_admin": {}}))
    class API:
        def login(self, credentials):
            return {}
        def close(self):
            pass
    def fail(api):
        raise RuntimeError("private-exception-token")
    monkeypatch.setattr(benchmark_native, "ROOT", tmp_path)
    monkeypatch.setattr(benchmark_native, "RUNTIME", runtime)
    monkeypatch.setattr(benchmark_native, "NexentAPI", API)
    monkeypatch.setattr(benchmark_native, "disable_technical_memory", fail)
    assert benchmark_native.main() == 1
    text = next((tmp_path / "evidence").glob("native-*/summary.json")).read_text()
    report = json.loads(text)
    assert report["failure_stage"] == "technical_memory_controls/tenant_admin"
    assert report["native_core_cases_executed"] == 0 and report["selected_model"] is None
    assert "private-exception-token" not in text + capsys.readouterr().out


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

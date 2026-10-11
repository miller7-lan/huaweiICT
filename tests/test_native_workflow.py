"""Exercise native request contracts, deployment expressions and evidence gates."""

import ast
import importlib.util
import json
import hashlib
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Dict, Optional

import httpx
import pytest

import nexent_api
from benchmark_native import check_loaded, check_parameters, run_payload
from nexent_api import NexentAPI, nonthinking_params
from prepare_deploy import bind_compose_ports, disabled_monitoring_env, services_only_script
from private_runtime import ensure_private_directory
from write_status import COMMIT, collect_status, receipts_valid
from verify_native_faults import connection_assessor, terminal_assessor


@pytest.mark.parametrize('flag', ['false', '"false"', "'false'"])
def test_disabled_monitoring_stops_fastapi_automatic_exporters(flag):
    text = f'ENABLE_TELEMETRY={flag}\nOTEL_EXPORTER_OTLP_PROTOCOL=http\nOTEL_TRACES_EXPORTER=otlp\n'
    result = disabled_monitoring_env(text)
    assert 'OTEL_TRACES_EXPORTER=none' in result and 'OTEL_METRICS_EXPORTER=none' in result
    assert 'OTEL_LOGS_EXPORTER=none' in result
    assert 'OTEL_EXPORTER_OTLP_PROTOCOL=http\n' in result
    assert disabled_monitoring_env(result) == result


def test_explicit_monitoring_configuration_is_preserved():
    text = 'ENABLE_TELEMETRY="true"\nOTEL_TRACES_EXPORTER=otlp\n'
    assert disabled_monitoring_env(text) == text


def test_service_deployment_account_step_cannot_register_or_login():
    root = Path(__file__).resolve().parents[1]
    original = (root / 'vendor/nexent/deploy/docker/deploy.sh').read_text(encoding='utf-8')
    generated = services_only_script(original)
    function = re.search(r'(?ms)^create_default_super_admin_user\(\) \{.*?^\}', generated).group()
    bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash:
        pytest.skip('Bash is unavailable')
    # Any accidental network/client invocation causes failure instead of signup.
    shell = 'curl() { exit 90; }; docker() { exit 91; }; bash() { exit 92; };\n'
    result = subprocess.run([bash, '-c', shell + function + '\ncreate_default_super_admin_user'], capture_output=True, timeout=5)
    assert result.returncode == 0 and b'user-run' in result.stdout


def test_unknown_upstream_bootstrap_contract_is_refused():
    with pytest.raises(ValueError):
        services_only_script('# Unrecognized deployment script\n')


def test_native_readiness_accepts_ui_locale_redirect(monkeypatch):
    import check_native_ready
    real_client = httpx.Client
    def respond(request):
        if request.url.port == 3000 and request.url.path == '/':
            return httpx.Response(307, headers={'Location': '/zh'})
        return httpx.Response(200)
    monkeypatch.setattr(check_native_ready.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(check_native_ready.shutil, 'which', lambda _: None)
    assert check_native_ready.probe_services()['ui'] is True


def test_config_and_conversation_requests_use_separate_services():
    seen = []
    def respond(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"data": {"conversation_id": 7}})
    api = NexentAPI()
    api.http.close()
    api.http = httpx.Client(transport=httpx.MockTransport(respond))
    api.token = "private-test-session"
    try:
        api.call("GET", "/model/list")
        api.runtime_call("PUT", "/conversation/create", {"title": "technical"})
    finally:
        api.close()
    assert [url for url, _ in seen] == ["http://127.0.0.1:5010/model/list", "http://127.0.0.1:5014/conversation/create"]
    assert all(header == "Bearer private-test-session" for _, header in seen)


def test_native_payload_omits_metadata_and_native_effort():
    state = {"agents": {"gov_coordinator": 1}, "models": {"model": 5}, "versions": {"gov_coordinator": 2}}
    payload = run_payload(state, "我是工作人员", 10, "model")
    assert payload["conversation_id"] == 10 and payload["version_no"] == 2
    assert not {"metadata", "reasoning_effort", "generation_config"}.intersection(payload)
    assert payload["enable_plan"] is False and payload["enable_automation_tool"] is False


def test_every_published_agent_pins_nonthinking_provider_fields(monkeypatch):
    calls = []
    class API:
        def call(self, method, path, body):
            calls.append((path, body))
            return {"version_no": len(calls)}
    monkeypatch.setattr(nexent_api, "save_state", lambda state: None)
    state = {"models": {"model": 7}, "agents": {"citizen_assistant": 2, "staff_assistant": 3, "gov_coordinator": 1}}
    nexent_api.activate_model(API(), state, "model")
    updates = [body for path, body in calls if path == "/agent/update"]
    assert len(updates) == 3
    assert all(body["few_shots_prompt"] and body["duty_prompt"] for body in updates)
    assert all(body["model_params_override"]["7"]["extra_params"] == nonthinking_params() for body in updates)
    assert [path for path, _ in calls if path.endswith("/publish")] == ["/agent/2/publish", "/agent/3/publish", "/agent/1/publish"]


def test_provider_none_survives_pinned_upstream_normalization():
    file = Path("vendor/nexent/backend/utils/reasoning.py")
    spec = importlib.util.spec_from_file_location("pinned_reasoning", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for capability in (None, {"status": "unsupported"}, {"status": "supported", "levels": ["low", "medium", "high"]}):
        normalized = module.normalize_reasoning_params(nonthinking_params(), capability)
        assert normalized["__custom__"]["reasoning_effort"] == "none"
        assert normalized.get("enable_thinking", False) is False
        tree = ast.parse(Path("vendor/nexent/backend/agents/create_agent_info.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_build_extra_body")
        ns = {"Optional": Optional, "Dict": Dict}
        from typing import Any
        ns["Any"] = Any
        exec(compile(ast.Module(body=[function], type_ignores=[]), "pinned_extra_body", "exec"), ns)
        wire = ns["_build_extra_body"](normalized)
        assert wire["reasoning_effort"] == "none"


@pytest.mark.parametrize("port,expected", [
    ('${OTEL_GRPC_PORT:-4317}:4317', '127.0.0.1:${OTEL_GRPC_PORT:-4317}:4317'),
    ('0.0.0.0:${PORT:-3000}:3000', '127.0.0.1:${PORT:-3000}:3000'),
    ('[::]:8000:8000/tcp', '127.0.0.1:8000:8000/tcp'),
    ('127.0.0.1:8765:8765', '127.0.0.1:8765:8765'),
    ('${SUPABASE_POSTGRES_PORT}:${SUPABASE_POSTGRES_PORT}', '127.0.0.1:${SUPABASE_POSTGRES_PORT}:${SUPABASE_POSTGRES_PORT}'),
])
def test_port_binding_preserves_compose_substitutions(port, expected):
    content = 'services:\n  service:\n    ports:\n      - "' + port + '" # technical\n    image: unchanged\n'
    value = bind_compose_ports(content)
    assert '"' + expected + '" # technical' in value
    assert 'image: unchanged' in value
    assert bind_compose_ports(value) == value


def test_loaded_model_and_all_pinned_parameters_are_required():
    info = {"parameters": "num_ctx 8192\ntemperature 0.1\nnum_predict 1024", "details": {"quantization_level": "Q4_K_M"}}
    assert check_parameters(info)
    assert not check_parameters({**info, "parameters": info["parameters"].replace('0.1', '0.9')})
    loaded = {"models": [{"name": "model", "context_length": 8192}]}
    assert check_loaded(loaded, "model")
    assert not check_loaded({"models": loaded["models"]*2}, "model")
    assert not check_loaded({"models": [{"name": "model", "context_length": 4096}]}, "model")


def test_private_directory_is_created_before_files(tmp_path):
    runtime = tmp_path / "private"
    ensure_private_directory(runtime)
    assert runtime.is_dir()
    (runtime / "technical-file").write_text("no credentials")
    if os.name == "nt":
        import win32security
        import ntsecuritycon
        for file in (runtime, runtime / "technical-file"):
            acl = win32security.GetNamedSecurityInfo(str(file), win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
            assert acl.GetAceCount() == 2
            assert all(acl.GetAce(i)[1] == ntsecuritycon.FILE_ALL_ACCESS for i in range(2))
            assert any(win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) == 'S-1-5-18' for i in range(2))


def test_status_reads_live_readiness_and_never_initializes_missing_business_db(tmp_path):
    live = {"docker_engine": "AVAILABLE", "wsl_ready": True, "services": {"config": True, "runtime": True}}
    status = collect_status(tmp_path, platform=live)
    assert status["platform"]["docker_engine"] == "AVAILABLE"
    assert status["blocker"]["code"] != "WSL_NOT_READY"
    assert not status["core_chain_accepted"] and status["business_counts"] is None
    assert not (tmp_path / ".runtime/mcp/business.sqlite3").exists()


def test_status_recomputes_native_completion_and_latest_failure(tmp_path):
    from test_acceptance import CHECKS, records
    dest = tmp_path / "evidence/native-test"
    dest.mkdir(parents=True)
    rows = records()
    for index, row in enumerate(rows):
        (dest / f"case-{index}.json").write_text(json.dumps(row))
    summary = {"status": "COMPLETE", "platform_commit": COMMIT, "selected_model": "gov-qwen3-4b:ctx8192-v1", "model_metadata": CHECKS}
    (dest / "summary.json").write_text(json.dumps(summary))
    live = {"docker_engine": "AVAILABLE", "wsl_ready": True, "services": {"runtime": True}}
    status = collect_status(tmp_path, live)
    assert status["core_chain_accepted"] and status["native_core_cases_executed"] == 40
    assert status["selected_model"] == "gov-qwen3-4b:ctx8192-v1"
    assert not status["native_ui_verified"] and not status["additional_fault_acceptance"]
    (dest / "summary.json").write_text(json.dumps({**summary, "status": "FAILED"}))
    status = collect_status(tmp_path, live)
    assert not status["core_chain_accepted"] and status["selected_model"] is None


def test_ui_receipt_requires_all_roles_and_unchanged_assets(tmp_path):
    assets = tmp_path / "evidence/native-ui/native-test"
    assets.mkdir(parents=True)
    record = {"passed": True, "conversation_id": 1}
    for name in ("screenshot", "trace"):
        file = assets / name
        file.write_bytes(b'technical fixture')
        record[name] = file.relative_to(tmp_path).as_posix()
        record[name + "_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    report = {"benchmark_reference": "native-test", "records": {"citizen": record}}
    assert not receipts_valid(report, ("citizen", "staff"), "native-test", tmp_path)
    assert receipts_valid(report, ("citizen",), "native-test", tmp_path)
    assert not receipts_valid(report, ("citizen",), "native-other", tmp_path)
    (assets / 'trace').write_bytes(b'changed')
    assert not receipts_valid(report, ("citizen",), "native-test", tmp_path)


def test_native_negative_result_requires_audit_identity_and_no_child():
    value = {"status": "BLOCKED", "message_code": "FORBIDDEN", "items": [], "citations": [],
             "knowledge_version": None, "data": {"request_id": "actual"}}
    events = [{"type": "tool", "tool_name": "check_case_prerequisites"}, {"type": "final_answer", "content": json.dumps(value)}]
    audit = [{"tool": "check_case_prerequisites", "principal_sha256": "trusted", "result": value}]
    evaluate = terminal_assessor("check_case_prerequisites", "BLOCKED", "FORBIDDEN")
    assert evaluate(events, audit, "citizen", "trusted")["passed"]
    assert not evaluate(events, audit, "citizen", "another")["passed"]
    assert not evaluate(events + [{"type": "subagent_start"}], audit, "citizen", "trusted")["passed"]
    assert not evaluate(events, [], "citizen", "trusted")["passed"]


def test_connection_outage_must_stop_without_tools_or_step_exhaustion():
    value = {"status": "ERROR", "message_code": "CONNECTION_TIMEOUT", "items": [], "citations": [], "knowledge_version": None}
    events = [{"type": "final_answer", "content": json.dumps(value)}]
    assert connection_assessor(events, [], "citizen", "trusted")["passed"]
    assert not connection_assessor(events, [{"tool": "get_environment_status"}], "citizen", "trusted")["passed"]
    assert not connection_assessor(events + [{"type": "max_steps_reached"}], [], "citizen", "trusted")["passed"]

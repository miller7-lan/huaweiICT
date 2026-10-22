"""Keep cloud platform readiness separate from the legacy dual-Qwen gate."""

import io
import json
import subprocess
import sys
import urllib.request
from types import SimpleNamespace

import httpx
import pytest

import check_native_ready as readiness


@pytest.fixture
def platform(monkeypatch, tmp_path):
    real_client = httpx.Client
    monkeypatch.setattr(readiness.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kwargs))
    monkeypatch.setattr(readiness, "sandbox_ready", lambda: True)
    monkeypatch.setattr(readiness.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(readiness, "__file__", str(tmp_path / "scripts/check_native_ready.py"))
    calls = []

    def unexpected_process(*args, **kwargs):
        calls.append(args[0])
        raise AssertionError("An ordinary platform probe must not contact Ollama")

    monkeypatch.setattr(readiness.subprocess, "run", unexpected_process)
    return tmp_path, calls


def test_default_platform_readiness_does_not_require_or_contact_ollama(platform):
    _, calls = platform
    checks = readiness.probe_services()
    assert all(checks.values())
    assert "container_ollama" not in checks
    assert not calls


def test_cloud_readiness_still_blocks_an_unready_sandbox(platform, monkeypatch):
    monkeypatch.setattr(readiness, "sandbox_ready", lambda: False)
    assert readiness.probe_services()["runtime_sandbox"] is False


@pytest.mark.parametrize("matches", [True, False])
def test_cloud_readiness_still_enforces_recorded_runtime_overlay(platform, monkeypatch, matches):
    root, calls = platform
    record = root / "evidence/runtime-error-overlay.json"
    record.parent.mkdir()
    record.write_text(json.dumps({"overlay_sha256": "expected-overlay"}), encoding="utf-8")

    def inspect_overlay(command, **kwargs):
        calls.append(command)
        assert "run_agent.py" in command[-1]
        assert "11434" not in command[-1]
        return SimpleNamespace(returncode=0, stdout="expected-overlay" if matches else "changed-overlay")

    monkeypatch.setattr(readiness.subprocess, "run", inspect_overlay)
    checks = readiness.probe_services()
    assert checks["runtime_error_overlay"] is matches
    assert all(checks.values()) is matches
    assert len(calls) == 1


@pytest.mark.parametrize("models,expected", [
    ([], False),
    (["gov-qwen3-4b:ctx8192-v1"], False),
    (["gov-qwen3-8b:ctx8192-v1"], False),
    (["gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1"], True),
])
def test_explicit_local_gate_requires_both_aliases_from_runtime(platform, monkeypatch, models, expected):
    _, calls = platform

    def model_listing(url, timeout):
        assert url == "http://host.docker.internal:11434/v1/models"
        assert timeout == 3
        return io.BytesIO(json.dumps({"data": [{"id": model} for model in models]}).encode())

    def container_python(command, **kwargs):
        calls.append(command)
        assert command[:5] == ["docker", "exec", "nexent-runtime", "python", "-c"]
        try:
            exec(compile(command[-1], "container-readiness", "exec"), {})
        except AssertionError:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(urllib.request, "urlopen", model_listing)
    monkeypatch.setattr(readiness.subprocess, "run", container_python)
    checks = readiness.probe_services(require_local_models=True)
    assert checks["container_ollama"] is expected
    assert all(checks.values()) is expected
    assert len(calls) == 1


def test_explicit_local_gate_fails_closed_when_container_probe_times_out(platform, monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 8)

    monkeypatch.setattr(readiness.subprocess, "run", timeout)
    assert readiness.probe_services(require_local_models=True)["container_ollama"] is False


@pytest.mark.parametrize("extra,expected", [([], False), (["--require-local-models"], True)])
def test_cli_passes_the_requested_readiness_scope(monkeypatch, capsys, extra, expected):
    requested = []

    def probe(*, require_local_models):
        requested.append(require_local_models)
        return {"runtime_sandbox": True}

    monkeypatch.setattr(sys, "argv", ["check_native_ready.py", *extra])
    monkeypatch.setattr(readiness, "probe_services", probe)
    assert readiness.main() == 0
    assert requested == [expected]
    assert json.loads(capsys.readouterr().out) == {"runtime_sandbox": True}

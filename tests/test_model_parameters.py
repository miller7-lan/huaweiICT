"""Parameter safety and trial boundaries; fixture calls never prove model quality."""
from copy import deepcopy
import json

import httpx
import pytest

import compare_model_parameters as trial
from gov_mcp.deepseek import CloudError, PROFILES, chat


@pytest.mark.parametrize("temperature", [True, False, None, "0.1", -0.1, 2.01, float("nan"), float("inf")])
def test_invalid_temperature_is_rejected_before_credentials_or_http(temperature):
    with pytest.raises(CloudError, match="INVALID_TEMPERATURE"):
        chat(PROFILES["official"], [], environment={}, temperature=temperature)


@pytest.mark.parametrize("temperature", [0, 0.1, 0.3, 2])
def test_effective_temperature_is_sent_with_nonthinking_json_mode(temperature):
    def handle(request):
        body = json.loads(request.content)
        assert body["temperature"] == temperature
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 1024
        assert body["response_format"] == {"type": "json_object"}
        assert "top_p" not in body
        return httpx.Response(200, json={"model": "deepseek-fixture", "choices": [
            {"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat(PROFILES["official"], [], client=client, environment={"DEEPSEEK_API_KEY": "fixture"}, temperature=temperature)


def test_judge_rejects_fabricated_unknown_relaxed_pause_and_boolean_numbers():
    samples = trial.cases()
    for case in samples[:3]:
        assert all(trial.assess(case, case["expected"]).values())
        bad = {**case["expected"], "published": True}
        assert not all(trial.assess(case, bad).values())
    bad = {**samples[0]["expected"], "advance_days": 30}
    assert not trial.assess(samples[0], bad)["advance_days"]
    bad = {**samples[1]["expected"], "status": "candidate", "duration_days": 15}
    assert not all(trial.assess(samples[1], bad).values())
    bad = {**samples[2]["expected"], "guardian_required": False}
    assert not trial.assess(samples[2], bad)["guardian_required"]
    bad = {**samples[0]["expected"], "published": 0}
    assert not trial.assess(samples[0], bad)["published"]


def setup_trial(monkeypatch, tmp_path, responder):
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(trial, "ensure_private_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(trial, "load_key", lambda _: "fixture")
    monkeypatch.setattr(trial, "chat", responder)


def test_external_failure_stops_all_calls_and_trial_cannot_restart(monkeypatch, tmp_path):
    sent = []
    def unavailable(*args, **kwargs):
        sent.append(kwargs)
        raise CloudError("DEEPSEEK_TRANSPORT_UNAVAILABLE")
    setup_trial(monkeypatch, tmp_path, unavailable)
    out = tmp_path / "evidence"
    result = trial.run_live(out, trial.plan())
    assert result["status"] == "blocked" and result["http_attempts"] == len(sent) == 1
    assert result["recommended_temperature"] is None
    with pytest.raises(ValueError, match="TRIAL_ALREADY_STARTED"):
        trial.run_live(out, trial.plan())
    (out / "results.json").unlink()
    with pytest.raises(ValueError, match="TRIAL_LEDGER_EXISTS"):
        trial.run_live(out, trial.plan())
    assert len(sent) == 1


def test_exact_call_budget_and_input_equality_between_temperatures(monkeypatch, tmp_path):
    sent = []
    def response(profile, messages, **kwargs):
        sent.append((deepcopy(messages), kwargs))
        return {"output": {}, "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "deepseek-fixture"}
    setup_trial(monkeypatch, tmp_path, response)
    result = trial.run_live(tmp_path / "evidence", trial.plan())
    assert result["status"] == "complete" and result["http_attempts"] == len(sent) == 12
    assert result["recommended_temperature"] is None
    for i in range(0, 12, 3):
        assert sent[i][0] == sent[i + 1][0] == sent[i + 2][0]
        assert {row[1]["temperature"] for row in sent[i:i + 3]} == {0, 0.1, 0.3}


def test_changed_served_model_prevents_recommendation():
    rows = [{"temperature": temp, "elapsed_seconds": 1, "passed": True, "checks": {"x": True},
             "result": {"model": model, "usage": {"input_tokens": 1, "output_tokens": 1}}}
            for temp, model in ((0, "deepseek-v1"), (0.1, "deepseek-v2"))]
    report = trial.summarize({"results": rows, "status": "complete"})
    assert report["recommended_temperature"] is None

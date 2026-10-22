"""Offline transport and live-suite contracts; no real provider calls in pytest."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest

from gov_mcp.classification import ClassificationError, JevProvider, classify_corpus, ENDPOINT as OFFICIAL_ENDPOINT
from gov_mcp.jev_gateway import VercelJevProvider, ENDPOINT, MODEL, GATEWAY_MODEL

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jev_live_runner", ROOT/"scripts/test_jev_live.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CONFIG = json.loads((ROOT/"config/preparation/classification.json").read_text("utf-8"))
CASES = json.loads((ROOT/"config/preparation/classification.examples.json").read_text("utf-8"))["cases"]


class Response(io.BytesIO):
    status = 200


def reply(payload):
    case = next((c for c in CASES if c["title"] == payload["state"].get("title")), None)
    answers = {}
    for key, question in payload["questions"].items():
        if question["type"] == "choice":
            choice = case["document_type"]
            answers[key] = {"type": "choice", "choice": choice, "confidence": .99,
                            "probabilities": {k: int(k == choice) for k in question["criteria"]}}
        else:
            selected = case[key.split(":")[-2]] if case else ["supported"]
            label = key.split(":")[-1]
            answers[key] = {"type": "noul", "noul": .99 if label in selected else .01}
    return {"model": GATEWAY_MODEL, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10},
            "provider_metadata": {"gateway": {"cost": "0.00001", "generationId": "gen-test", "routing": {"finalProvider": "typesafe-ai"}}}}


def fake_opener(request, timeout):
    assert request.full_url == ENDPOINT and timeout == 30
    payload = json.loads(request.data)
    assert payload["model"] == GATEWAY_MODEL
    assert payload["providerOptions"] == {"gateway": {"only": ["typesafe-ai"]}}
    return Response(json.dumps(reply(payload)).encode())


QUESTIONS = {"supported": {"type": "noul", "instructions": "有明确支持吗？"}}


def test_native_contract_is_preserved_with_actual_gateway_model_and_cost():
    provider = VercelJevProvider("test-secret", opener=fake_opener)
    value = provider.evaluate({"text": "模拟资料"}, QUESTIONS, MODEL)
    assert value["model"] == MODEL and value["answers"]["supported"]["noul"] == .99
    assert provider.receipts[0]["served_model"] == GATEWAY_MODEL
    assert provider.receipts[0]["cost_usd"] == "0.00001"
    assert "test-secret" not in str(provider.receipts)


def test_missing_key_and_unpinned_model_do_not_send():
    p = VercelJevProvider("", opener=lambda *a, **k: pytest.fail("network attempted"))
    with pytest.raises(ClassificationError, match="MISSING_API_KEY"): p.evaluate({}, QUESTIONS, MODEL)
    with pytest.raises(ClassificationError, match="MODEL_NOT_PINNED"): p.evaluate({}, QUESTIONS, "jev-latest")
    assert p.attempts == 0


@pytest.mark.parametrize("code,expected", [(401,"AUTHENTICATION_FAILED"),(402,"PAYMENT_REQUIRED"),(403,"ACCESS_DENIED"),(429,"RATE_LIMITED"),(500,"UPSTREAM_HTTP_ERROR")])
def test_upstream_error_stops_subsequent_calls_without_leaking_body(code, expected):
    def bad(request, timeout): raise urllib.error.HTTPError(ENDPOINT, code, "secret exception text", {}, io.BytesIO(b"secret body"))
    p = VercelJevProvider("secret", opener=bad)
    for _ in range(2):
        with pytest.raises(ClassificationError, match=expected): p.evaluate({}, QUESTIONS, MODEL)
    assert p.attempts == 1 and "secret" not in str(p.receipts)


@pytest.mark.parametrize("message,reason", [
    ("Free tier users do not have access to this model", "FREE_TIER_MODEL_RESTRICTED"),
    ("customer_verification_required", "CUSTOMER_VERIFICATION_REQUIRED"),
    ("access_denied", "ACCESS_DENIED"),
    ("unknown secret response", "UNRECOGNIZED_JSON_ERROR"),
])
def test_http_diagnostics_keep_only_fixed_categories(message, reason):
    def bad(request, timeout):
        body = json.dumps({"error": {"message": message, "credential": "test-secret"}}).encode()
        raise urllib.error.HTTPError(ENDPOINT, 403, "secret", {}, io.BytesIO(body))
    p = VercelJevProvider("test-secret", opener=bad)
    with pytest.raises(ClassificationError, match="ACCESS_DENIED"): p.evaluate({}, QUESTIONS, MODEL)
    assert p.receipts[0]["upstream_reason"] == reason
    assert "test-secret" not in str(p.receipts) and message not in str(p.receipts)


@pytest.mark.parametrize("mutation", ["fallback", "probability", "missing_confidence", "nan_cost"])
def test_invalid_gateway_answers_never_pass(mutation):
    def bad(request, timeout):
        value = reply(json.loads(request.data))
        if mutation == "fallback": value["model"] = "another/model"
        elif mutation == "probability": value["answers"]["supported"]["noul"] = 1.1
        elif mutation == "nan_cost": value["provider_metadata"]["gateway"]["cost"] = "NaN"
        else:
            # Choice confidence cannot be substituted with selected probability.
            value["answers"] = {"supported": {"type": "choice", "choice": "yes", "probabilities": {"yes": 1, "no": 0}}}
        return Response(json.dumps(value).encode())
    qs = {"supported": {"type": "choice", "criteria": {"yes": "是", "no": "否"}}} if mutation == "missing_confidence" else QUESTIONS
    p = VercelJevProvider("test", opener=bad)
    with pytest.raises(ClassificationError): p.evaluate({}, qs, MODEL)
    assert p.receipts[0]["status"] == "error"


@pytest.mark.parametrize("malformed", ["duplicate_noul", "duplicate_metadata", "unused_nan", "overflow", "array"])
def test_gateway_raw_json_rejects_ambiguous_and_nonfinite_values_without_retry(malformed):
    def opener(request, timeout):
        value = reply(json.loads(request.data))
        raw = json.dumps(value)
        if malformed == "duplicate_noul":
            raw = raw.replace('"noul": 0.99', '"noul": 0.01, "noul": 0.99')
            assert '"noul": 0.01, "noul": 0.99' in raw
        elif malformed == "duplicate_metadata":
            raw = raw.replace('"cost": "0.00001"', '"cost": "0.10", "cost": "0.00001"')
            assert '"cost": "0.10", "cost": "0.00001"' in raw
        elif malformed == "unused_nan":
            value["provider_metadata"]["unused"] = [float("nan")]
            raw = json.dumps(value)
        elif malformed == "overflow":
            raw = raw.replace('"generationId": "gen-test"', '"generationId": "gen-test", "unused": [1e999]')
        else:
            raw = '[]'
        return Response(raw.encode())
    provider = VercelJevProvider("fixture-secret", opener=opener, max_calls=2)
    for _ in range(2):
        with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
            provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.attempts == 1 and len(provider.receipts) == 1
    assert provider.receipts[0]["error_code"] == "INVALID_RESPONSE"
    assert provider.receipts[0]["status"] == "error"
    assert provider.known_cost == 0
    assert "fixture-secret" not in str(provider.receipts) and "noul" not in str(provider.receipts)


def test_request_and_count_budgets_stop_before_network():
    p = VercelJevProvider("test", opener=fake_opener, max_calls=1)
    with pytest.raises(ClassificationError, match="MODEL_INPUT_TOO_LARGE"): p.evaluate({"text": "字"*6000}, QUESTIONS, MODEL)
    assert p.attempts == 0
    p.evaluate({}, QUESTIONS, MODEL)
    with pytest.raises(ClassificationError, match="TEST_BUDGET_EXHAUSTED"): p.evaluate({}, QUESTIONS, MODEL)
    assert p.attempts == 1


def test_private_material_never_leaves_existing_classification_boundary():
    corpus = runner.corpus_for(CASES[:1])
    corpus["data_class"] = "private"
    p = VercelJevProvider("test", opener=lambda *a, **k: pytest.fail("private material sent"))
    run = classify_corpus(corpus, CONFIG, p)
    assert p.attempts == 0 and run["summary"]["status"] == "error"
    assert run["records"][0]["routes"] == ["pending_review"]


def test_complete_live_suite_contract_uses_19_bounded_fake_requests():
    p = VercelJevProvider("test", opener=fake_opener)
    report = runner.run_suite(p)
    assert report["status"] == "completed" and report["http_attempts"] == 19
    assert report["metrics"]["document_types"]["correct"] == 9
    assert report["all_originals_preserved"] and report["option_order"]["changed"] == 0
    assert report["cost_complete"] and report["support_smoke"]["all_expected"]
    assert report["other_model_calls"] == 0 and not report["business_rules_published"]


def test_missing_credential_creates_an_honest_blocked_report():
    p = VercelJevProvider("")
    report = runner.run_suite(p)
    assert report["status"] == "blocked" and report["error_code"] == "MISSING_API_KEY"
    assert report["http_attempts"] == 0 and not report["cost_complete"]


def official_opener(request, timeout):
    payload = json.loads(request.data)
    assert request.full_url == OFFICIAL_ENDPOINT and timeout == 30
    assert payload["model"] == MODEL and "providerOptions" not in payload
    value = reply(payload)
    value["model"] = MODEL
    value.pop("provider_metadata")
    return Response(json.dumps(value).encode())


def test_official_suite_preserves_usage_without_inventing_billing():
    provider = JevProvider("official-test-secret", opener=official_opener, max_calls=32)
    report = runner.run_suite(provider)
    assert report["status"] == "completed" and report["http_attempts"] == 19
    assert report["endpoint"] == OFFICIAL_ENDPOINT and report["transport_model"] == MODEL
    assert report["metrics"]["document_types"]["correct"] == 9
    assert report["all_originals_preserved"] and report["other_model_calls"] == 0
    assert report["known_success_cost_usd"] is None and not report["cost_complete"]
    assert all(r["usage"]["input_tokens"] == 100 for r in report["receipts"])
    assert "official-test-secret" not in json.dumps(report)


def test_official_credentials_never_fall_back_to_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gateway-only-secret")
    key_file = tmp_path/"credentials.json"
    key_file.write_text(json.dumps({"AI_GATEWAY_API_KEY": "wrong-provider"}), encoding="utf-8")
    provider = runner.create_provider("jev", key_file)
    provider.open = lambda *a, **k: pytest.fail("Missing official key must not call a provider")
    report = runner.run_suite(provider)
    assert report["status"] == "blocked" and report["http_attempts"] == 0
    assert report["error_code"] == "MISSING_API_KEY" and not provider.api_key
    monkeypatch.setenv("TYPESAFE_API_KEY", "official-env-secret")
    assert runner.create_provider("jev", key_file).api_key == "official-env-secret"


def test_official_key_file_is_explicit_and_provider_specific(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    key_file = tmp_path/"official.json"
    key_file.write_text(json.dumps({"TYPESAFE_API_KEY": "official-file-secret"}), encoding="utf-8")
    assert runner.create_provider("jev", key_file).api_key == "official-file-secret"


def test_official_retries_share_the_http_budget():
    def overloaded(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 529, "private error", {"Retry-After": "0"}, io.BytesIO(b"secret"))
    provider = JevProvider("test", opener=overloaded, sleep=lambda _: None, max_calls=1)
    with pytest.raises(ClassificationError, match="TEST_BUDGET_EXHAUSTED"):
        provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.attempts == 1 and len(provider.receipts) == 1
    assert "private error" not in str(provider.receipts) and "secret" not in str(provider.receipts)


def test_official_wrong_model_and_private_input_cannot_count_as_success():
    def wrong_model(request, timeout):
        value = reply(json.loads(request.data))
        return Response(json.dumps(value).encode())  # Gateway-qualified model is not the official ID.
    provider = JevProvider("test", opener=wrong_model, max_calls=32)
    with pytest.raises(ClassificationError, match="INVALID_RESPONSE"):
        provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.receipts[0]["status"] == "error"
    private = runner.corpus_for(CASES[:1])
    private["data_class"] = "private"
    provider.open = lambda *a, **k: pytest.fail("Private material sent")
    run = classify_corpus(private, CONFIG, provider)
    assert provider.attempts == 1 and run["summary"]["status"] == "error"


def test_official_transport_honors_configured_proxy_and_blocks_redirects(monkeypatch):
    import urllib.request
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    provider = JevProvider("test")
    handlers = provider.open.__self__.handlers
    proxy = next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
    assert proxy.proxies["https"] == "http://127.0.0.1:7897"
    redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    assert redirect.redirect_request(None, None, 302, "redirect", {}, "https://other.example/") is None


def test_official_access_restriction_is_blocked_after_one_request():
    def restricted(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 451, "private upstream message", {}, io.BytesIO(b"private body"))
    provider = JevProvider("test", opener=restricted, max_calls=32)
    report = runner.run_suite(provider)
    assert report["status"] == "blocked" and report["error_code"] == "ACCESS_RESTRICTED"
    with pytest.raises(ClassificationError, match="ACCESS_RESTRICTED"):
        provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.attempts == 1 and provider.receipts[0]["http_status"] == 451
    assert "private upstream message" not in str(report) and "private body" not in str(report)

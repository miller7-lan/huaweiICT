"""Jev JSON regression boundaries; fixtures do not measure model accuracy."""

from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path

import pytest

from gov_mcp.classification import ClassificationError, JevProvider, validate_response


MODEL = "jev-1.13.0"
QUESTIONS = {"supported": {"type": "noul", "instructions": "Is the statement supported?"}}


def response():
    return {"model": MODEL, "answers": {"supported": {"type": "noul", "noul": .9}},
            "usage": {"input_tokens": 10, "output_tokens": 2}}


@pytest.mark.parametrize("raw", [
    # Conflicting values must be rejected before the later value can win.
    '{"model":"jev-1.13.0","answers":{"supported":{"type":"noul","noul":0.1,"noul":0.9}},"usage":{"input_tokens":10,"output_tokens":2}}',
    '{"model":"jev-1.13.0","model":"jev-1.13.0","answers":{},"usage":{"input_tokens":10,"output_tokens":2}}',
    '{"model":"jev-1.13.0","answers":{"supported":{"type":"noul","noul":0.9,"unused":NaN}},"usage":{"input_tokens":10,"output_tokens":2}}',
    '{"model":"jev-1.13.0","answers":{"supported":{"type":"noul","noul":0.9}},"usage":{"input_tokens":10,"output_tokens":2},"provider_metadata":{"unused":[Infinity]}}',
    '{"model":"jev-1.13.0","answers":{"supported":{"type":"noul","noul":0.9}},"usage":{"input_tokens":10,"output_tokens":2},"provider_metadata":{"unused":[1e999]}}',
    '[]',
])
def test_official_raw_ambiguous_or_nonfinite_json_is_rejected_once(raw):
    provider = JevProvider("fixture-secret", opener=lambda *args, **kwargs: io.BytesIO(raw.encode()),
                           sleep=lambda _: pytest.fail("Invalid JSON must not trigger retries"), max_calls=1)
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.attempts == 1
    assert len(provider.receipts) == 1
    assert provider.receipts[0]["status"] == "error"
    assert provider.receipts[0]["error_code"] == "INVALID_RESPONSE"
    assert "fixture-secret" not in str(provider.receipts) and "noul" not in str(provider.receipts)
    with pytest.raises(ClassificationError, match="^TEST_BUDGET_EXHAUSTED$"):
        provider.evaluate({}, QUESTIONS, MODEL)
    assert provider.attempts == 1


@pytest.mark.parametrize("location", ["root", "noul", "usage", "choice"])
def test_undeclared_fields_fail_instead_of_being_retained(location):
    value = response()
    questions = QUESTIONS
    if location == "root": value["unexpected"] = True
    elif location == "noul": value["answers"]["supported"]["unused"] = "extra"
    elif location == "usage": value["usage"]["total_tokens"] = 12
    else:
        questions = {"supported": {"type": "choice", "criteria": {"yes": "yes", "no": "no"}}}
        value["answers"]["supported"] = {"type": "choice", "choice": "yes", "confidence": .9,
                                         "probabilities": {"yes": .9, "no": .1}, "unused": "extra"}
    before = deepcopy(value)
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, questions, MODEL)
    assert value == before


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), ("tuple",),
                                  b"bytes", {1: "non-string-key"}, datetime(2026, 10, 21), {"a", "b"}])
def test_direct_adapter_metadata_cannot_smuggle_non_json_or_nonfinite_values(bad):
    value = response()
    value["provider_metadata"] = {"gateway": {"diagnostic": [bad]}}
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, QUESTIONS, MODEL)


@pytest.mark.parametrize("location", ["root", "noul", "usage", "english_input"])
def test_direct_extra_nested_nan_is_always_rejected(location):
    value = response()
    nested = {"unexpected": [float("nan")]}
    if location == "root": value["unused"] = nested
    elif location == "noul": value["answers"]["supported"]["unused"] = nested
    elif location == "usage": value["usage"]["unused"] = nested
    else:
        value["english_input"] = {"version": "jev-english-1", "state": nested, "questions": {}}
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, QUESTIONS, MODEL)


@pytest.mark.parametrize("location", ["noul", "input_tokens", "unknown_question_type", "unknown_audit_key"])
def test_direct_types_and_declared_audit_shape_stay_strict(location):
    value = response()
    questions = deepcopy(QUESTIONS)
    if location == "noul": value["answers"]["supported"]["noul"] = True
    elif location == "input_tokens": value["usage"]["input_tokens"] = True
    elif location == "unknown_question_type":
        questions["supported"]["type"] = value["answers"]["supported"]["type"] = "unknown"
    else:
        value["english_input"] = {"version": "jev-english-1", "state": {}, "questions": {}, "extra": True}
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, questions, MODEL)


def test_declared_supplier_metadata_and_local_translation_audit_are_preserved():
    # The existing Vercel adapter exposes this native metadata namespace; its
    # fields are supplier extensions, distinct from Jev's decision fields.
    value = response()
    value["provider_metadata"] = {"gateway": {"cost": "0.00001", "generationId": "gen-fixture",
        "routing": {"finalProvider": "typesafe-ai", "providerTrace": ["fixture", {"latency": 1.25}]}}}
    value["english_input"] = {"version": "jev-english-1", "state": {"text": "An application form is required."},
                              "questions": deepcopy(QUESTIONS)}
    before = deepcopy(value)
    checked = validate_response(value, QUESTIONS, MODEL)
    assert checked == before == value and checked is not value
    assert checked["provider_metadata"] == before["provider_metadata"]
    assert checked["english_input"] == before["english_input"]


def test_metadata_copy_recursion_failure_is_sanitized(monkeypatch):
    value = response()
    value["provider_metadata"] = {"gateway": {"routing": ["valid JSON"]}}

    def cannot_copy(_):
        raise RecursionError("fixture-private-upstream-text")

    monkeypatch.setattr("gov_mcp.classification.deepcopy", cannot_copy)
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, QUESTIONS, MODEL)


def test_archived_real_official_response_fields_remain_accepted():
    # Reuse existing public synthetic evidence, without contacting Jev or
    # claiming that replay is a new real-model validation.
    path = Path(__file__).resolve().parents[1] / "evidence/jev-official/proxy-smoke-20261018T073750195325Z.json"
    archived = json.loads(path.read_text("utf-8"))
    value = {"model": archived["served_model"], "answers": archived["answers"], "usage": archived["usage"]}
    questions = {key: {"type": "noul"} for key in value["answers"]}
    assert validate_response(value, questions, MODEL) == value


def test_large_invalid_integer_and_cyclic_metadata_fail_with_sanitized_error():
    value = response()
    value["answers"]["supported"]["noul"] = 10 ** 1000
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, QUESTIONS, MODEL)
    value = response()
    metadata = {}
    metadata["cycle"] = metadata
    value["provider_metadata"] = metadata
    with pytest.raises(ClassificationError, match="^INVALID_RESPONSE$"):
        validate_response(value, QUESTIONS, MODEL)

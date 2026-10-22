"""Reproduce local JSON boundaries without real HTTP or model calls.

This attachment diagnoses implementation behavior only. It does not measure a
model's JSON compliance rate, business correctness, or competition acceptance.
Run with the project venv Python; only core-probes.json is created beside it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import socket
import sys


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import httpx
import pydantic

from gov_mcp.classification import validate_response
from gov_mcp.deepseek import CloudError, PROFILES, chat
from gov_mcp.jev_translation import translation_contract
from gov_mcp.json_output import StrictOutput, object_from_json, validate_contract


def blocked_network(*args, **kwargs):
    raise AssertionError("REAL_NETWORK_FORBIDDEN_IN_OFFLINE_PROBE")


# Defense in depth: every chat below also injects a MockTransport client and
# fixture-only environment. No configured credential files are consulted.
socket.socket.connect = blocked_network
socket.socket.connect_ex = blocked_network
socket.create_connection = blocked_network


class ProbeOutput(StrictOutput):
    count: int
    published: bool


class FloatOutput(StrictOutput):
    value: float


mock_requests = []


def mock_chat(*, content="{}", message_override="default", response_model=None):
    message = {"content": content} if message_override == "default" else message_override

    def handler(request):
        mock_requests.append({"method": request.method, "path": request.url.path})
        return httpx.Response(200, json={
            "model": "deepseek-offline-fixture",
            "choices": [{"finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        return chat(PROFILES["official"], [{"role": "system", "content": "Offline fixture only."}],
                    client=client, environment={"DEEPSEEK_API_KEY": "offline-fixture-not-a-key"},
                    response_model=response_model)


def outcome(action):
    try:
        action()
        return {"returned": True}
    except Exception as error:
        return {"returned": False, "exception_type": type(error).__name__,
                "error_code": str(error) if isinstance(error, (CloudError, ValueError))
                and not isinstance(error, pydantic.ValidationError) else None}


def run():
    questions = {"support": {"type": "noul", "criterion": "supported"}}
    duplicate_raw = ('{"model":"jev-1.13.0","answers":{"support":'
                     '{"type":"noul","noul":0.1,"noul":0.9}},'
                     '"usage":{"input_tokens":1,"output_tokens":1}}')
    duplicate = validate_response(json.loads(duplicate_raw), questions, "jev-1.13.0")
    loose_raw = ('{"model":"jev-1.13.0","answers":{"support":'
                 '{"type":"noul","noul":0.9,"unused":NaN}},'
                 '"usage":{"input_tokens":1,"output_tokens":1},"unexpected":true}')
    loose = validate_response(json.loads(loose_raw), questions, "jev-1.13.0")

    findings = [{
        "id": "jev_duplicate_key_overwritten",
        "status": "reproduced_gap",
        "source_locations": ["src/gov_mcp/classification.py:93", "src/gov_mcp/classification.py:255"],
        "probe_kind": "pure_function_sequence_used_by_provider",
        "first_noul": 0.1, "last_noul": 0.9,
        "accepted_noul": duplicate["answers"]["support"]["noul"],
        "duplicate_was_not_rejected": True,
        "scope": "Jev's JSON parse precedes field validation; no Jev request was sent.",
    }, {
        "id": "jev_extra_fields_and_unused_nonfinite_retained",
        "status": "reproduced_gap",
        "source_locations": ["src/gov_mcp/classification.py:93", "src/gov_mcp/classification.py:255"],
        "probe_kind": "pure_function_sequence_used_by_provider",
        "extra_top_level_field_retained": "unexpected" in loose,
        "unused_nested_nan_retained": math.isnan(loose["answers"]["support"]["unused"]),
        "required_noul_valid": loose["answers"]["support"]["noul"] == 0.9,
        "scope": "Used probabilities remain validated; this demonstrates unvalidated extra fields only.",
    }]

    for name, message in (("null", None), ("array", [])):
        observed = outcome(lambda: mock_chat(message_override=message))
        assert observed["exception_type"] == "AttributeError", observed
        findings.append({
            "id": "deepseek_message_" + name + "_escapes_cloud_error",
            "status": "reproduced_gap",
            "source_locations": ["src/gov_mcp/deepseek.py:96", "src/gov_mcp/deepseek.py:131",
                                 "src/gov_mcp/decision/connections.py:113"],
            "probe_kind": "httpx.MockTransport_only",
            "observed": observed,
            "scope": "Malformed response raises AttributeError instead of sanitized CloudError; no valid candidate is returned.",
        })

    parser_checks = []
    for name, raw, code in [
        ("duplicate_key", '{"x":1,"x":2}', "DUPLICATE_JSON_KEY"),
        ("nested_duplicate", '{"x":{"y":1,"y":2}}', "DUPLICATE_JSON_KEY"),
        ("nested_nan", '{"x":[NaN]}', "NONFINITE_JSON_NUMBER"),
        ("float_overflow", '{"x":1e999}', "NONFINITE_JSON_NUMBER"),
        ("array_root", '[]', "JSON_OBJECT_REQUIRED"),
    ]:
        observed = outcome(lambda: object_from_json(raw))
        assert observed.get("error_code") == code, observed
        parser_checks.append({"id": name, "observed": observed, "expected_block_observed": True})

    schema_checks = []
    for name, value in [
        ("extra_field", {"count": 1, "published": False, "type": "json_object"}),
        ("missing_field", {"published": False}),
        ("boolean_as_integer", {"count": True, "published": False}),
        ("integer_as_boolean", {"count": 1, "published": 0}),
        ("float_as_integer", {"count": 1.0, "published": False}),
    ]:
        observed = outcome(lambda: validate_contract(value, ProbeOutput))
        assert observed.get("exception_type") == "ValidationError", observed
        schema_checks.append({"id": name, "observed": observed, "expected_block_observed": True})
    valid = {"count": 1, "published": False}
    assert validate_contract(valid, ProbeOutput) is valid
    translated = {"translations": {"model_config": "original", "slot_1": "original"}}
    assert validate_contract(translated, translation_contract({"model_config": "原文", "slot_1": "原文"})) is translated

    no_contract = mock_chat(content='{"count":"wrong-type","published":0,"unexpected":true}')
    contract_violation = outcome(lambda: mock_chat(
        content='{"count":"wrong-type","published":0,"unexpected":true}', response_model=ProbeOutput))
    assert contract_violation["error_code"] == "DEEPSEEK_OUTPUT_CONTRACT_VIOLATION"

    integer = object_from_json('{"value":1}')
    decimal = object_from_json('{"value":1.0}')
    float_integer = {"value": 1}
    assert validate_contract(float_integer, FloatOutput) is float_integer
    underflow = object_from_json('{"value":1e-999}')
    large_integer = object_from_json('{"value":9007199254740993}')
    rounded_float = object_from_json('{"value":9007199254740993.0}')

    source_paths = ["src/gov_mcp/json_output.py", "src/gov_mcp/deepseek.py",
                    "src/gov_mcp/classification.py", "src/gov_mcp/jev_translation.py",
                    "src/gov_mcp/decision/connections.py", "src/gov_mcp/decision/intake.py",
                    "scripts/compare_model_parameters.py"]
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline reproduction of JSON implementation boundaries and confirmed gaps.",
        "limitations": ["No real network, model generation, business acceptance, or model compliance rate measured.",
                        "Existing evidence, business sources, model settings, and budgets were not changed.",
                        "Findings indicate local parse/contract behavior, not proof of unauthorized publication."],
        "runtime": {"python": platform.python_version(), "pydantic": pydantic.__version__, "httpx": httpx.__version__},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "execution": {"real_network_allowed": False, "real_network_requests": 0,
                      "mock_transport_requests": len(mock_requests), "mock_request_routes": mock_requests,
                      "credentials": "injected fixture environment only; no credential files read"},
        "findings": findings,
        "strict_boundary_observations": {"parser": parser_checks, "schema": schema_checks,
            "valid_object_returned_without_normalizing": True,
            "arbitrary_translation_aliases_supported": True,
            "without_response_model_wrong_shape_returned_as_untrusted_candidate":
                no_contract["status"] == "untrusted_machine_candidate" and no_contract["output"]["published"] == 0,
            "with_response_model_wrong_shape_blocked": contract_violation},
        "numeric_representation_boundaries": {
            "python_dict_equality_does_not_prove_json_types": integer == decimal and {"x": True} == {"x": 1},
            "integer_literal_type": type(integer["value"]).__name__,
            "decimal_literal_type": type(decimal["value"]).__name__,
            "strict_float_field_accepts_integer_as_json_number": type(float_integer["value"]) is int,
            "strict_validation_returns_original_object": True,
            "small_nonzero_decimal_underflows_to_zero": underflow["value"] == 0.0,
            "large_integer_literal_remains_exact": large_integer["value"] == 9007199254740993,
            "large_decimal_literal_rounds_in_binary_float": rounded_float["value"] == 9007199254740992.0,
            "scope": "Python float representation boundary; finite-value and Schema checks do not promise exact decimal preservation. JSON Schema number includes integer values.",
        },
    }


if __name__ == "__main__":
    destination = Path(__file__).with_suffix(".json")
    if destination.exists():
        raise SystemExit("REFUSING_TO_OVERWRITE_EXISTING_CORE_PROBES_EVIDENCE")
    result = run()
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "real_network_requests": 0,
                      "mock_transport_requests": result["execution"]["mock_transport_requests"],
                      "reproduced_gap_ids": [item["id"] for item in result["findings"]]}, ensure_ascii=False))

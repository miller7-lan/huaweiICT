"""Output boundary checks. Mock responses do not measure model compliance."""
from copy import deepcopy
import json

import httpx
import pytest
from pydantic import ValidationError

import compare_model_parameters as trial
from gov_mcp.classification import ClassificationError
from gov_mcp.deepseek import CloudError, PROFILES, ROOT, chat
from gov_mcp.jev_translation import DeepSeekTranslator, EnglishJevProvider, translation_contract
from gov_mcp.json_output import contract_messages, validate_contract


def call(raw, response_model=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "deepseek-fixture", "choices": [
            {"finish_reason": "stop", "message": {"content": raw}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return chat(PROFILES["official"], trial.messages(trial.cases()[0]), client=client,
                    environment={"DEEPSEEK_API_KEY": "fixture"}, response_model=response_model)


@pytest.mark.parametrize("raw", [
    '{"published":true,"published":false}',
    '{"translations":{"text_0":"first","text_0":"second"}}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
    '{"nested":[1e999]}', '[]', '```json\n{}\n```',
])
def test_ambiguous_or_non_json_responses_are_rejected_without_contract(raw):
    seen = []
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_OUTPUT$"):
        call(raw, seen=seen)
    assert len(seen) == 1


@pytest.mark.parametrize("change", ["extra", "missing", "numeric_bool", "bool_integer", "nested_type"])
def test_contract_rejects_fields_and_types_without_repair_or_retry(change):
    case = trial.cases()[0]
    output = deepcopy(case["expected"])
    if change == "extra": output["type"] = "json_object"
    elif change == "missing": output.pop("advance_days")
    elif change == "numeric_bool": output["published"] = 0
    elif change == "bool_integer": output["advance_days"] = True
    else: output["material_quote"] = [output["material_quote"]]
    before = deepcopy(output)
    seen = []
    with pytest.raises(CloudError, match="^DEEPSEEK_OUTPUT_CONTRACT_VIOLATION$") as rejected:
        call(json.dumps(output), trial.response_contract(case), seen)
    assert output == before and len(seen) == 1
    assert rejected.value.candidate_result["output"] == before
    assert rejected.value.candidate_result["usage"] == {"input_tokens": 1, "output_tokens": 1}


def test_contract_prompt_preserves_source_messages_and_metadata_stays_protocol_only():
    case = trial.cases()[0]
    original = trial.messages(case)
    before = deepcopy(original)
    prepared = contract_messages(original, trial.response_contract(case))
    assert original == before and prepared[1] == original[1]
    assert "not an answer field" in prepared[0]["content"]
    seen = []
    assert call(json.dumps(case["expected"]), trial.response_contract(case), seen)["output"] == case["expected"]
    assert seen[0]["messages"] == prepared
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "response_model" not in seen[0]


@pytest.mark.parametrize("model,json_output", [(dict, True), (trial.QuotationOutput, False)])
def test_invalid_contract_is_rejected_before_credential_lookup(monkeypatch, model, json_output):
    monkeypatch.setattr("gov_mcp.deepseek.load_key", lambda *args: pytest.fail("credential lookup"))
    with pytest.raises(CloudError, match="^INVALID_OUTPUT_CONTRACT$"):
        chat(PROFILES["official"], [], response_model=model, json_output=json_output)


def test_contract_is_not_a_truth_or_publication_approval():
    case = trial.cases()[0]
    false_fact = {**case["expected"], "advance_days": 30, "published": True}
    assert validate_contract(false_fact, trial.response_contract(case)) is false_fact
    assert not all(trial.assess(case, false_fact).values())


def test_translation_requires_exact_nested_keys_with_arbitrary_aliases():
    model = translation_contract({"__root__": "原文", "slot_1": "原文", "model_config": "原文"})
    valid = {"translations": {"__root__": "original", "slot_1": "original", "model_config": "original"}}
    assert validate_contract(valid, model) is valid
    for malformed in ({**valid, "type": "json_object"},
                      {"translations": {**valid["translations"], "unexpected": "extra"}},
                      {"translations": {"__root__": "original"}}):
        with pytest.raises(ValidationError): validate_contract(malformed, model)


def test_invalid_cloud_translation_cannot_reach_jev_or_populate_cache():
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"model": "deepseek-fixture", "choices": [
            {"finish_reason": "stop", "message": {"content":
                '{"translations":{"text_0":"original"},"type":"json_object"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    class NoJev:
        name = "offline-fixture"
        def evaluate(self, *args): pytest.fail("invalid translation reached Jev")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        translator = DeepSeekTranslator(client=client, environment={"DEEPSEEK_API_KEY": "fixture"}, enabled=True)
        adapter = EnglishJevProvider(NoJev(), translator)
        with pytest.raises(ClassificationError, match="^TRANSLATION_UNAVAILABLE$"):
            adapter.evaluate({"text": "原文"}, {}, "jev-1.13.0")
    assert len(sent) == translator.attempts == 1 and not adapter.cache


def test_original_plan_and_failed_observations_stay_frozen():
    historical = ROOT / "evidence/model-parameters-20261019"
    assert trial.plan() == json.loads((historical / "plan.json").read_text("utf-8"))
    observations = json.loads((historical / "results.json").read_text("utf-8"))["results"]
    lookup = {case["id"]: case for case in trial.cases()}
    rejected = []
    for row in observations:
        output = row["result"]["output"]
        try:
            assert validate_contract(output, trial.response_contract(lookup[row["case_id"]])) is output
        except ValidationError:
            rejected.append(row["case_id"])
            assert output["type"] == "json_object" and row["passed"] is False
    assert rejected == ["quotation_unknown_injection"] * 3


def test_new_strict_trial_has_four_calls_and_preserves_semantic_checks(monkeypatch, tmp_path):
    sent = []
    def response(profile, messages, **kwargs):
        sent.append(kwargs)
        assert kwargs["response_model"].model_config["extra"] == "forbid"
        return {"output": {}, "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "deepseek-fixture"}
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(trial, "load_key", lambda _: "fixture")
    monkeypatch.setattr(trial, "ensure_private_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(trial, "chat", response)
    frozen = trial.plan(strict_json=True)
    assert frozen["max_http_attempts"] == len(frozen["schedule"]) == 4
    assert frozen["cases"] == trial.cases()
    result = trial.run_live(tmp_path / "evidence", frozen)
    assert len(sent) == result["http_attempts"] == 4 and result["recommended_temperature"] is None
    assert all(row["passed"] is False for row in result["results"])
    assert {kwargs["temperature"] for kwargs in sent} == {0.0}


def test_contract_failure_stops_new_trial_without_reopening_budget(monkeypatch, tmp_path):
    sent = []
    def rejected(*args, **kwargs):
        sent.append(kwargs)
        error = CloudError("DEEPSEEK_OUTPUT_CONTRACT_VIOLATION")
        error.candidate_result = {"output": {**trial.cases()[0]["expected"], "type": "json_object"},
                                  "model": "deepseek-fixture", "usage": {"input_tokens": 1, "output_tokens": 1}}
        raise error
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(trial, "load_key", lambda _: "fixture")
    monkeypatch.setattr(trial, "ensure_private_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(trial, "chat", rejected)
    out = tmp_path / "evidence"
    result = trial.run_live(out, trial.plan(strict_json=True))
    assert result["status"] == "blocked" and result["http_attempts"] == len(sent) == 1
    row = result["results"][0]
    assert row["result"]["output"]["type"] == "json_object" and row["output_contract_valid"] is False
    assert row["checks"]["exact_field_set"] is False
    with pytest.raises(ValueError, match="TRIAL_ALREADY_STARTED"):
        trial.run_live(out, trial.plan(strict_json=True))
    assert len(sent) == 1

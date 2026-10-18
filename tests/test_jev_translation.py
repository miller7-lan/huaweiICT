"""Translation boundary tests; every language-model response is an offline fixture."""

from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from gov_mcp.classification import ClassificationError, JevProvider, classify_corpus, configured_provider, with_english_input
from gov_mcp.jev_translation import DeepSeekTranslator, EnglishJevProvider, CJK, NUMBERS


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/preparation/classification.json").read_text("utf-8"))
SOURCE = "申请时须提交申请表，免收费，5个工作日办结。"
ENGLISH = "An application form must be submitted. No fee is charged. Processing takes 5 working days."


def corpus():
    return {"data_class": "synthetic", "assets": [{"asset_id": "资料-1", "source_id": "来源-1", "sha256": "source-hash",
        "title": "模拟办事指南", "modality": "text", "segments": [{"asset_id": "资料-1", "segment_id": "片段-1",
        "content": SOURCE, "locator": {"kind": "line", "line": 1}}]}]}


class RecordingJev:
    name = "offline-jev-transport"

    def __init__(self):
        self.calls, self.attempts = [], 0

    def evaluate(self, state, questions, model):
        self.calls.append((deepcopy(state), deepcopy(questions), model))
        self.attempts += 1
        answers = {}
        for key, question in questions.items():
            if question["type"] == "choice":
                answers[key] = {"type": "choice", "choice": "guide", "confidence": .99,
                    "probabilities": {label: float(label == "guide") for label in question["criteria"]}}
            else:
                answers[key] = {"type": "noul", "noul": .99 if key.endswith(("domains:identity", "purposes:material")) else .01}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 80, "output_tokens": 0}}


class TranslationFixture:
    def __init__(self, mutate=None):
        self.calls, self.mutate = [], mutate

    def translate(self, texts):
        self.calls.append(deepcopy(texts))
        translations = {key: ENGLISH if source == SOURCE else "English translation " + " ".join(NUMBERS.findall(source))
                        for key, source in texts.items()}
        result = {"output": {"translations": translations}, "model": "deepseek-translation-fixture",
                  "usage": {"input_tokens": 40, "output_tokens": 50}}
        if self.mutate:
            self.mutate(result, texts)
        return result


def test_english_classification_preserves_originals_identifiers_routes_and_separate_usage():
    original = corpus()
    before = deepcopy(original)
    jev, translator = RecordingJev(), TranslationFixture()
    provider = EnglishJevProvider(jev, translator)
    run = classify_corpus(original, CONFIG, provider)
    assert run["summary"]["status"] == "completed"
    assert original == before
    state, questions, model = jev.calls[0]
    assert state["segments"][0] == {"segment_id": "片段-1", "content": ENGLISH}
    assert model == CONFIG["model"]
    assert set(questions) == set(translator_question_ids())
    assert all(not CJK.search(question["instructions"]) for question in questions.values())
    assert set(questions["document_type"]["criteria"]) == set(CONFIG["document_types"])
    assert all(not CJK.search(text) for question in questions.values() for text in question["criteria"].values())
    assert run["bundles"]["guide_sections"][0]["content"] == SOURCE
    assert run["records"][0]["segment_id"] == "片段-1"
    assert run["summary"]["usage"] == {"input_tokens": 80, "output_tokens": 0}
    assert run["summary"]["translation"]["usage"] == {"input_tokens": 40, "output_tokens": 50}
    receipt = run["calls"][0]["input_translation"][0]
    assert run["calls"][0]["english_input"]["state"]["segments"][0]["content"] == ENGLISH
    assert receipt["source_state_sha256"] != receipt["state_sha256"]
    assert ENGLISH not in json.dumps(receipt) and SOURCE not in json.dumps(receipt)


def translator_question_ids():
    from gov_mcp.classification import batch_questions
    return batch_questions(CONFIG, [{"content": SOURCE, "segment_id": "片段-1"}])


def test_successful_translation_is_cached_without_changing_question_order():
    jev, translator = RecordingJev(), TranslationFixture()
    provider = EnglishJevProvider(jev, translator)
    left = classify_corpus(corpus(), CONFIG, provider)
    right = classify_corpus(corpus(), CONFIG, provider)
    assert len(translator.calls) == 1 and len(jev.calls) == 2
    assert right["summary"]["translation"]["calls"] == 0
    assert right["summary"]["translation"]["actual_models"] == ["deepseek-translation-fixture"]
    assert left["summary"]["translation"]["calls"] == 1
    assert list(jev.calls[0][1]) == list(jev.calls[1][1])


@pytest.mark.parametrize("location", ["segment", "locator", "metadata"])
@pytest.mark.parametrize("parent_class", ["synthetic", "public"])
def test_private_descendants_never_reach_translation_or_jev(location, parent_class):
    original = corpus()
    original["data_class"] = parent_class
    segment = original["assets"][0]["segments"][0]
    if location == "segment": segment["data_class"] = "private"
    elif location == "locator": segment["locator"]["hidden"] = [{"data_class": "private", "content": SOURCE}]
    else: original["metadata"] = {"data_class": "private", "text": SOURCE}
    before = deepcopy(original)
    jev, translator = RecordingJev(), TranslationFixture()
    run = classify_corpus(original, CONFIG, EnglishJevProvider(jev, translator), allow_public=True)
    assert not jev.calls and not translator.calls
    assert run["records"][0]["reasons"] == ["DATA_CLASS_BLOCKED"]
    assert original == before and all("english_input" not in call for call in run["calls"])


def test_public_and_inherited_segments_still_translate_with_explicit_opt_in():
    original = corpus()
    original["data_class"] = "public"
    jev, translator = RecordingJev(), TranslationFixture()
    run = classify_corpus(original, CONFIG, EnglishJevProvider(jev, translator), allow_public=True)
    assert run["summary"]["status"] == "completed" and jev.calls and translator.calls


def test_private_input_cannot_reuse_successful_translation_cache():
    original = corpus()
    jev, translator = RecordingJev(), TranslationFixture()
    provider = EnglishJevProvider(jev, translator)
    classify_corpus(original, CONFIG, provider)
    before = (len(jev.calls), len(translator.calls))
    original["assets"][0]["segments"][0]["data_class"] = "private"
    run = classify_corpus(original, CONFIG, provider)
    assert run["records"][0]["reasons"] == ["DATA_CLASS_BLOCKED"]
    assert (len(jev.calls), len(translator.calls)) == before


@pytest.mark.parametrize("private_scope", ["corpus", "asset"])
def test_data_gate_runs_before_translation_and_jev(private_scope):
    data = corpus()
    (data if private_scope == "corpus" else data["assets"][0])["data_class"] = "private"
    jev, translator = RecordingJev(), TranslationFixture()
    run = classify_corpus(data, CONFIG, EnglishJevProvider(jev, translator))
    assert run["summary"]["error_codes"] == {"DATA_CLASS_BLOCKED": 1}
    assert not jev.calls and not translator.calls


@pytest.mark.parametrize("mutation", ["missing", "extra", "empty", "chinese", "number", "usage", "model"])
def test_invalid_translation_never_reaches_jev_or_falls_back_to_chinese(mutation):
    def mutate(result, texts):
        translated = result["output"]["translations"]
        key = next(key for key, value in texts.items() if value == SOURCE)
        if mutation == "missing": translated.pop(key)
        elif mutation == "extra": translated["unexpected"] = "extra"
        elif mutation == "empty": translated[key] = " "
        elif mutation == "chinese": translated[key] = SOURCE
        elif mutation == "number": translated[key] = ENGLISH.replace("5", "15")
        elif mutation == "usage": result["usage"]["input_tokens"] = True
        else: result["model"] = "secret invalid upstream model body"
    jev = RecordingJev()
    run = classify_corpus(corpus(), CONFIG, EnglishJevProvider(jev, TranslationFixture(mutate)))
    assert run["summary"]["status"] == "error" and not jev.calls
    assert run["records"][0]["routes"] == ["pending_review"]
    assert run["bundles"]["pending_review"][0]["content"] == SOURCE
    assert "secret invalid" not in json.dumps(run)
    assert run["records"][0]["reasons"] == ["TRANSLATION_NUMBERS_CHANGED" if mutation == "number" else "TRANSLATION_INVALID_RESPONSE"]


def test_translated_payload_is_size_checked_before_jev():
    text = "模拟内容" * 100
    jev = RecordingJev()
    def expand(result, texts):
        result["output"]["translations"] = {key: "English " * 500 if source == text else "English rule"
                                              for key, source in texts.items()}
    provider = EnglishJevProvider(jev, TranslationFixture(expand), max_request_bytes=1024)
    with pytest.raises(ClassificationError, match="TRANSLATED_INPUT_TOO_LARGE"):
        provider.evaluate({"text": text}, {"test": {"type": "noul", "instructions": "判断"}}, CONFIG["model"])
    assert not jev.calls


def test_support_evidence_and_claims_translate_together_but_references_are_opaque():
    state = {"claims": [{"claim_id": "候选-1", "name": "申请表", "quote": SOURCE, "source_segment_ids": ["片段-1"]}],
             "evidence": {"片段-1": {"segment_id": "片段-1", "content": SOURCE, "locator": {"label": "第1行"}}}}
    before = deepcopy(state)
    jev = RecordingJev()
    provider = EnglishJevProvider(jev, TranslationFixture())
    provider.evaluate(state, {"0": {"type": "noul", "instructions": "依据引用原文判断支持情况"}}, CONFIG["model"])
    translated = jev.calls[0][0]
    assert translated["claims"][0]["quote"] == translated["evidence"]["片段-1"]["content"] == ENGLISH
    assert translated["claims"][0]["claim_id"] == "候选-1"
    assert translated["claims"][0]["source_segment_ids"] == ["片段-1"]
    assert translated["evidence"]["片段-1"]["locator"] == {"label": "第1行"}
    assert state == before


def test_existing_english_input_does_not_call_translation():
    jev, translator = RecordingJev(), TranslationFixture()
    provider = EnglishJevProvider(jev, translator)
    provider.evaluate({"text": "An application form is required."},
                      {"support": {"type": "noul", "instructions": "Is a form required?"}}, CONFIG["model"])
    assert not translator.calls
    assert provider.translation_receipts[0]["status"] == "already_english"


@pytest.mark.parametrize("enabled,environment,code", [(False, {"DEEPSEEK_API_KEY": "fixture"}, "TRANSLATION_INFERENCE_DISABLED"),
    (True, {}, "TRANSLATION_UNAVAILABLE")])
def test_global_gate_and_missing_credentials_prevent_translation_http(enabled, environment, code):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP request")))
    translator = DeepSeekTranslator(client=client, environment=environment, enabled=enabled)
    with pytest.raises(ClassificationError, match=code): translator.translate({"text_0": SOURCE})
    assert translator.attempts == 0


def test_deepseek_translation_transport_has_explicit_english_prompt_and_larger_output_budget():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        assert body["max_tokens"] == 8192 and body["response_format"] == {"type": "json_object"}
        assert "negation" in body["messages"][0]["content"] and "untrusted" in body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"]) == {"text_0": SOURCE}
        return httpx.Response(200, json={"model": "deepseek-test", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"translations": {"text_0": ENGLISH}})}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30}})
    translator = DeepSeekTranslator(client=httpx.Client(transport=httpx.MockTransport(handler)),
                                   environment={"DEEPSEEK_API_KEY": "fixture"}, enabled=True)
    assert translator.translate({"text_0": SOURCE})["output"]["translations"]["text_0"] == ENGLISH
    assert translator.attempts == 1


def test_truncated_cloud_translation_is_not_used():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "deepseek-test", "choices": [{"finish_reason": "length"}]})))
    translator = DeepSeekTranslator(client=client, environment={"DEEPSEEK_API_KEY": "fixture"}, enabled=True)
    jev = RecordingJev()
    with pytest.raises(ClassificationError, match="TRANSLATION_UNAVAILABLE"):
        EnglishJevProvider(jev, translator).evaluate({"text": SOURCE}, {"support": {"type": "noul"}}, CONFIG["model"])
    assert not jev.calls


def test_default_factory_wraps_jev_and_explicit_replay_stays_offline():
    provider = configured_provider(CONFIG)
    assert isinstance(provider, EnglishJevProvider)
    raw = configured_provider({**CONFIG, "translation": {"enabled": False}})
    assert not isinstance(raw, EnglishJevProvider)
    replay = RecordingJev()
    run = classify_corpus(corpus(), CONFIG, replay)
    assert run["summary"]["translation"]["applied"] is False


def test_default_translation_respects_disabled_inference_and_sends_no_model_requests(monkeypatch):
    monkeypatch.setattr("gov_mcp.jev_translation.inference_enabled", lambda: False)
    jev = JevProvider("fixture", opener=lambda *args, **kwargs: pytest.fail("Jev called while translation disabled"))
    run = classify_corpus(corpus(), CONFIG, with_english_input(jev, CONFIG))
    assert run["records"][0]["reasons"] == ["TRANSLATION_INFERENCE_DISABLED"]
    assert run["summary"]["translation"]["calls"] == 0
    assert jev.attempts == 0


def test_missing_jev_credential_prevents_unnecessary_translation():
    translator = TranslationFixture()
    provider = EnglishJevProvider(JevProvider(""), translator)
    run = classify_corpus(corpus(), CONFIG, provider)
    assert run["records"][0]["reasons"] == ["MISSING_API_KEY"]
    assert not translator.calls


def test_failed_translation_does_not_populate_cache():
    translator = TranslationFixture(lambda result, texts: result["output"]["translations"].clear())
    provider = EnglishJevProvider(RecordingJev(), translator)
    classify_corpus(corpus(), CONFIG, provider)
    assert not provider.cache


@pytest.mark.parametrize("status", [401, 402, 403])
def test_translation_authentication_or_payment_failure_stops_repeated_http(status):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="private upstream failure")
    translator = DeepSeekTranslator(client=httpx.Client(transport=httpx.MockTransport(handler)),
        environment={"DEEPSEEK_API_KEY": "fixture"}, enabled=True)
    provider = EnglishJevProvider(RecordingJev(), translator)
    for _ in range(2):
        run = classify_corpus(corpus(), CONFIG, provider)
        assert run["records"][0]["reasons"] == ["TRANSLATION_UNAVAILABLE"]
    assert len(seen) == 1 and translator.attempts == 1


@pytest.mark.parametrize("reason", ["blocked", "budget", "model"])
def test_unavailable_jev_is_checked_before_any_translation(reason):
    from gov_mcp.jev_gateway import VercelJevProvider
    provider = VercelJevProvider("fixture", opener=lambda *args, **kwargs: pytest.fail("Unexpected Jev HTTP"), max_calls=1)
    if reason == "blocked": provider.blocked_error = "ACCESS_DENIED"
    if reason == "budget": provider.attempts = 1
    translator = TranslationFixture()
    wrapped = EnglishJevProvider(provider, translator)
    with pytest.raises(ClassificationError, match={"blocked": "ACCESS_DENIED", "budget": "TEST_BUDGET_EXHAUSTED", "model": "MODEL_NOT_PINNED"}[reason]):
        wrapped.evaluate({"text": SOURCE}, {"support": {"type": "noul"}}, "wrong" if reason == "model" else CONFIG["model"])
    assert not translator.calls


@pytest.mark.parametrize("gateway,code,expected", [(False,403,"ACCESS_DENIED"), (True,403,"ACCESS_DENIED"), (False,451,"ACCESS_RESTRICTED")])
def test_jev_denial_after_first_request_prevents_later_translation(gateway, code, expected):
    import io
    import urllib.error
    from gov_mcp.jev_gateway import VercelJevProvider
    sent = []
    def denied(request, timeout):
        sent.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "private upstream message", {}, io.BytesIO(b"private"))
    raw = VercelJevProvider("fixture", opener=denied) if gateway else JevProvider("fixture", opener=denied)
    translator = TranslationFixture()
    provider = EnglishJevProvider(raw, translator)
    first = classify_corpus(corpus(), CONFIG, provider)
    initial_calls = len(translator.calls)
    second_corpus = corpus()
    second_corpus["assets"][0]["title"] = "另一个不同的标题"
    second = classify_corpus(second_corpus, CONFIG, provider)
    assert initial_calls > 0 and len(translator.calls) == initial_calls
    assert len(sent) == 1
    assert first["records"][0]["reasons"] == second["records"][0]["reasons"] == [expected]


@pytest.mark.parametrize("budget", [True, 0, 8193])
def test_invalid_translation_output_budget_fails_before_http(budget):
    from gov_mcp.deepseek import CloudError, PROFILES, chat
    with pytest.raises(CloudError, match="INVALID_OUTPUT_TOKEN_BUDGET"):
        chat(PROFILES["official"], [], environment={}, max_output_tokens=budget)


@pytest.mark.parametrize("translation", [None, {"enabled": "yes"}, {"enabled": True, "provider": "other", "target_language": "en"}])
def test_invalid_translation_configuration_is_rejected(translation):
    with pytest.raises(ValueError):
        classify_corpus(corpus(), {**CONFIG, "translation": translation}, RecordingJev())

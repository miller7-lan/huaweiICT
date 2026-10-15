"""Contract/routing tests use explicit fake decisions, never model accuracy claims."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import urllib.error

import pytest

from gov_mcp.classification import (ClassificationError, JevProvider, classify_corpus,
    document_windows, fingerprint, questions_for, save_run)

ROOT = Path(__file__).parents[1]
CONFIG = json.loads((ROOT / "config/preparation/classification.json").read_text(encoding="utf-8"))
EXAMPLES = json.loads((ROOT / "config/preparation/classification.examples.json").read_text(encoding="utf-8"))["cases"]


def corpus_for(cases=EXAMPLES):
    return {"data_class": "synthetic", "dataset_id": "classification-contract-fixture", "assets": [
        {"asset_id": case["id"], "source_id": case["id"], "sha256": hashlib.sha256(case["text"].encode()).hexdigest(),
         "title": case["title"], "modality": "text", "segments": [
             {"asset_id": case["id"], "segment_id": case["id"] + "-s1", "content": case["text"],
              "locator": {"kind": "line", "line": 1}}]} for case in cases]}


def fake_response(questions, choice="guide", domains=("identity",), purposes=("material",), model="jev-1.13.0"):
    answers = {}
    for key, q in questions.items():
        if q["type"] == "choice":
            answers[key] = {"type": "choice", "choice": choice, "confidence": .99,
                            "probabilities": {k: 1.0 if k == choice else 0.0 for k in q["criteria"]}}
        else:
            group, label = key.split(":")
            answers[key] = {"type": "noul", "noul": .99 if label in (domains if group == "domains" else purposes) else .01}
    return {"model": model, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}}


class ReplayProvider:
    name = "contract_replay_not_jev"

    def evaluate(self, state, questions, model):
        case = next(c for c in EXAMPLES if c["title"] == state["title"])
        return fake_response(questions, case["document_type"], case["domains"], case["purposes"], model)


def test_replay_routes_multilabel_and_preserves_every_original(tmp_path):
    corpus = corpus_for()
    before = deepcopy(corpus)
    run = classify_corpus(corpus, CONFIG, ReplayProvider())
    assert corpus == before and run["source_sha256"] == fingerprint(corpus)
    for record, case in zip(run["records"], EXAMPLES):
        assert record["routes"] == case["routes"]
        assert record["domains"]["labels"] == [k for k in CONFIG["domains"] if k in case["domains"]]
        assert record["domains"]["confidence"] is None
        assert record["review_status"] == "machine_candidate"
    union = {item["segment_id"] for rows in run["bundles"].values() for item in rows}
    assert union == {case["id"] + "-s1" for case in EXAMPLES}
    originals = {s["segment_id"]: s for a in corpus["assets"] for s in a["segments"]}
    for rows in run["bundles"].values():
        assert len(rows) == len({item["segment_id"] for item in rows})
        for item in rows:
            assert item["content"] == originals[item["segment_id"]]["content"]
            assert item["locator"] == originals[item["segment_id"]]["locator"]
    path = save_run(run, tmp_path)
    assert json.loads((path / "bundles.json").read_text(encoding="utf-8")) == run["bundles"]
    with pytest.raises(FileExistsError):
        save_run(run, tmp_path)


def test_reversed_options_are_forwarded_without_changing_label_identity():
    questions = questions_for(CONFIG, "document", True)
    assert list(questions["document_type"]["criteria"]) == list(reversed(CONFIG["document_types"]))
    left = classify_corpus(corpus_for(), CONFIG, ReplayProvider())
    right = classify_corpus(corpus_for(), CONFIG, ReplayProvider(), True)
    assert [r["routes"] for r in left["records"]] == [r["routes"] for r in right["records"]]


@pytest.mark.parametrize("data_class", ["public", "private", None])
def test_non_synthetic_never_reaches_provider(data_class):
    class Forbidden:
        name = "forbidden"
        def evaluate(self, *args):
            pytest.fail("No non-synthetic network call allowed")
    corpus = corpus_for(EXAMPLES[:1])
    corpus["data_class"] = data_class
    run = classify_corpus(corpus, CONFIG, Forbidden())
    assert run["records"][0]["reasons"] == ["DATA_CLASS_BLOCKED"]
    assert run["summary"]["http_attempts"] == 0


def test_private_asset_override_is_blocked():
    corpus = corpus_for(EXAMPLES[:1])
    corpus["assets"][0]["data_class"] = "private"
    run = classify_corpus(corpus, CONFIG, JevProvider(api_key=""))
    assert run["records"][0]["reasons"] == ["DATA_CLASS_BLOCKED"]


def test_missing_key_is_not_success_and_does_not_touch_network():
    def forbidden(*args, **kwargs):
        pytest.fail("Missing key must not reach network")
    run = classify_corpus(corpus_for(), CONFIG, JevProvider(api_key="", opener=forbidden))
    assert run["summary"]["status_counts"] == {"error": len(EXAMPLES)}
    assert run["summary"]["error_codes"] == {"MISSING_API_KEY": len(EXAMPLES)}
    assert all(r["routes"] == ["pending_review"] for r in run["records"])


@pytest.mark.parametrize("mutation", ["nan", "bool", "negative", "sum", "unknown", "missing", "extra", "model", "type", "confidence", "usage", "not_max"])
def test_corrupt_provider_output_goes_to_review(mutation):
    class Corrupt:
        name = "corrupt"
        def evaluate(self, state, questions, model):
            value = fake_response(questions)
            answer = value["answers"]["document_type"]
            if mutation in ("nan", "bool", "negative"):
                answer["probabilities"]["guide"] = {"nan": float("nan"), "bool": True, "negative": -.1}[mutation]
            elif mutation == "sum": answer["probabilities"]["guide"] = .3
            elif mutation == "unknown": answer["choice"] = "made-up-label"
            elif mutation == "missing": value["answers"].pop("domains:identity")
            elif mutation == "extra": value["answers"]["extra"] = {}
            elif mutation == "model": value["model"] = "changed-model"
            elif mutation == "type": answer["type"] = "noul"
            elif mutation == "confidence": answer["confidence"] = 2
            elif mutation == "usage": value["usage"]["input_tokens"] = -1
            elif mutation == "not_max": answer["choice"] = "form"
            return value
    run = classify_corpus(corpus_for(EXAMPLES[:1]), CONFIG, Corrupt())
    assert run["records"][0]["reasons"] == ["INVALID_RESPONSE"]
    assert run["records"][0]["routes"] == ["pending_review"]


@pytest.mark.parametrize("mode", ["low_choice", "ambiguous_domain", "ambiguous_purpose", "no_purpose", "unknown_domain"])
def test_uncertain_decisions_do_not_auto_route(mode):
    class Uncertain:
        name = "uncertain"
        def evaluate(self, state, questions, model):
            value = fake_response(questions)
            a = value["answers"]
            if "document_type" in a:
                if mode == "low_choice": a["document_type"]["confidence"] = .5
                if mode == "ambiguous_domain": a["domains:health"]["noul"] = .5
                if mode == "unknown_domain": a["domains:unknown"]["noul"] = .99
            else:
                if mode == "ambiguous_purpose": a["purposes:filling"]["noul"] = .5
                if mode == "no_purpose": a["purposes:material"]["noul"] = .01
            return value
    run = classify_corpus(corpus_for(EXAMPLES[:1]), CONFIG, Uncertain())
    assert run["records"][0]["routes"] == ["pending_review"]


def test_windows_cover_full_document_without_truncation():
    corpus = corpus_for(EXAMPLES[:1])
    asset = corpus["assets"][0]
    asset["segments"] = [{"segment_id": str(i), "content": str(i) * 4000} for i in range(3)]
    windows = document_windows(asset, 6000)
    assert [s["segment_id"] for w in windows for s in w] == ["0", "1", "2"]
    asset["segments"][0]["content"] = "x" * 6001
    with pytest.raises(ValueError, match="re-chunk"):
        document_windows(asset, 6000)


def test_duplicate_segments_rejected_before_any_request():
    corpus = corpus_for(EXAMPLES[:2])
    corpus["assets"][1]["segments"][0]["segment_id"] = corpus["assets"][0]["segments"][0]["segment_id"]
    with pytest.raises(ValueError, match="identity"):
        classify_corpus(corpus, CONFIG, ReplayProvider())


def test_request_uses_official_schema_utf8_and_fixed_model():
    def opener(request, timeout):
        body = json.loads(request.data)
        assert request.full_url == "https://api.typesafe.ai/v1/systemone"
        assert request.get_header("Authorization") == "Bearer local-test-only"
        assert timeout == 30 and body["model"] == "jev-1.13.0"
        assert body["state"]["target"]["content"] == "中文申请表"
        assert "不要补充" in next(iter(body["questions"].values()))["instructions"]
        return io.BytesIO(json.dumps(fake_response(body["questions"])).encode())
    provider = JevProvider(api_key="local-test-only", opener=opener)
    provider.evaluate({"target": {"content": "中文申请表"}}, questions_for(CONFIG, "segment"), CONFIG["model"])
    assert provider.attempts == 1


@pytest.mark.parametrize("code,expected_attempts", [(401, 1), (403, 1), (422, 1), (429, 2), (503, 2)])
def test_http_failures_have_bounded_retries_and_no_secret_leaks(code, expected_attempts):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "secret-do-not-log", {"Retry-After": "0"}, io.BytesIO(b"secret"))
    provider = JevProvider(api_key="local-test-only", opener=opener, sleep=lambda _: None)
    run = classify_corpus(corpus_for(EXAMPLES[:1]), CONFIG, provider)
    assert provider.attempts == expected_attempts
    assert "secret" not in json.dumps(run)
    assert run["summary"]["http_attempts"] == expected_attempts
    assert run["records"][0]["status"] == "error"


def test_timeout_is_not_success():
    def opener(*args, **kwargs): raise TimeoutError("private detail")
    run = classify_corpus(corpus_for(EXAMPLES[:1]), CONFIG, JevProvider(api_key="test", opener=opener))
    assert run["summary"]["error_codes"] == {"TRANSPORT_UNAVAILABLE": 1}


def test_changed_rules_are_fingerprinted_even_without_version_bump():
    config = deepcopy(CONFIG)
    config["purposes"]["material"] += " 新边界。"
    run = classify_corpus(corpus_for(EXAMPLES[:1]), config, ReplayProvider())
    assert run["rules_sha256"] != fingerprint(CONFIG)


@pytest.mark.parametrize("retry_after", ["120", "NaN", "Fri, 01 Jan 2100 00:00:00 GMT"])
def test_long_retry_after_defers_instead_of_ignoring_server_wait(retry_after):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "limited", {"Retry-After": retry_after}, io.BytesIO())
    provider = JevProvider(api_key="test", opener=opener, sleep=lambda _: pytest.fail("Do not block on long waits"))
    with pytest.raises(ClassificationError, match="RATE_LIMITED"):
        provider.evaluate({}, {}, CONFIG["model"])
    assert provider.attempts == 1


def test_long_mixed_document_routes_each_window_without_losing_segments():
    corpus = corpus_for(EXAMPLES[:1])
    asset = corpus["assets"][0]
    asset["segments"] = [{"asset_id": asset["asset_id"], "segment_id": f"s{i}",
        "content": marker * 4000, "locator": {"kind": "line", "line": i+1}}
        for i, marker in enumerate(("规", "指", "告"))]
    class Mixed:
        name = "mixed-contract-replay"
        def evaluate(self, state, questions, model):
            choice = {"规": "policy", "指": "guide", "告": "notice"}[state["segments"][0]["content"][0]] if "segments" in state else "guide"
            return fake_response(questions, choice)
    run = classify_corpus(corpus, CONFIG, Mixed())
    assert len(run["documents"][0]["windows"]) == 3
    assert [r["routes"] for r in run["records"]] == [
        ["guide_sections", "policy_clauses"], ["guide_sections"], ["guide_sections", "notice_faq"]]
    assert len(run["bundles"]["guide_sections"]) == 3


@pytest.mark.parametrize("bad_config", [False, True])
def test_preparation_embeds_after_optional_classification_failure(tmp_path, monkeypatch, bad_config):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("prep_integration", ROOT / "scripts/prepare_topic3_data.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    monkeypatch.setattr(prep, "ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    (tmp_path / "source.txt").write_text("模拟申请表需要本人签字。", encoding="utf-8")
    (tmp_path / "profile.json").write_text(json.dumps({"output_directory": ".runtime/preparation/run"}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"data_class": "synthetic", "dataset_id": "test",
        "assets": [{"source_id": "test-source", "title": "模拟文本", "modality": "text", "path": "source.txt"}]}), encoding="utf-8")
    if not bad_config:
        (tmp_path / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    snapshot = tmp_path / ".runtime/asset-query/snapshot/index.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"immutable previous snapshot")
    calls = []
    def embedding(profile, segments):
        calls.append(segments)
        if not bad_config:
            assert list((tmp_path / ".runtime/preparation/classification").glob("*/classification.json"))
        return {"model": "test", "dimension": 2, "vector_count": 2, "ranked": [{}]}
    monkeypatch.setattr(prep, "embed", embedding)
    monkeypatch.setattr(sys, "argv", ["prepare", "--manifest", "manifest.json", "--profile", "profile.json",
        "--classify", "--classification-config", "config.json", "--embed"])
    assert prep.main() == 0
    report = json.loads(next((tmp_path / "evidence/toolchain").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert report["input_preparation_passed"] is True
    assert report["classification"]["status"] == "error"
    assert len(calls) == 1 and calls[0][0]["content"] == "模拟申请表需要本人签字。"
    assert snapshot.read_bytes() == b"immutable previous snapshot"

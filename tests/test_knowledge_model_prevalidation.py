import json

import pytest

import verify_knowledge_model_prevalidation as preview
from gov_mcp.knowledge import fingerprint


EVIDENCE = [{"evidence_id": "E-real", "text": "原件OCR：20巧年；25个自然日。"}]


def answer():
    return {"behavior": "conditional_analysis", "answer": "原文写25个自然日，尚不能保证本地当前结果。",
            "citations": [{"evidence_id": "E-real", "quote": "25个自然日"}], "unresolved": ["当前本地执行"]}


def test_model_input_does_not_include_gold_or_rubric():
    case = {"question": "何时领证？", "evidence": EVIDENCE, "ai_reference_answer": "GOLD_SECRET",
            "expected_behavior": "GOLD_BEHAVIOR", "unresolved": ["GOLD_GAP"], "rubric": "GOLD_RULE"}
    payload = preview.model_payload("already-installed-model", case)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "GOLD_" not in serialized
    assert json.loads(payload["messages"][1]["content"]) == {"question": case["question"], "evidence": EVIDENCE}
    assert "tools" not in payload and payload["think"] is False


def test_accepts_exact_quote_but_not_corrected_ocr_or_concatenation():
    value = answer()
    assert preview.validate_answer(value, EVIDENCE) == []
    value["citations"][0]["quote"] = "2015年"
    assert "quote_not_exact_source_substring" in preview.validate_answer(value, EVIDENCE)
    value["citations"][0]["quote"] = "20巧年25个自然日"
    assert "quote_not_exact_source_substring" in preview.validate_answer(value, EVIDENCE)


def test_rejects_cross_case_citation_and_duplicate_citations():
    value = answer()
    value["citations"][0]["evidence_id"] = "E-from-another-question"
    assert "citation_outside_input" in preview.validate_answer(value, EVIDENCE)
    value = answer()
    value["citations"].append(dict(value["citations"][0]))
    assert "duplicate_citation" in preview.validate_answer(value, EVIDENCE)


def test_rejects_extra_privileged_fields_and_oversized_answers():
    value = answer() | {"publish": True}
    assert preview.validate_answer(value, EVIDENCE) == ["invalid_answer_fields"]
    value = answer()
    value["answer"] = "字" * 401
    value["unresolved"] = [True]
    assert set(preview.validate_answer(value, EVIDENCE)) == {"invalid_answer_length", "invalid_unresolved_items"}


@pytest.mark.parametrize("run_id", ["../other", "20261016T000000Z-12345678/child", "", "C:\\other"])
def test_rejects_unbounded_run_paths(run_id):
    with pytest.raises(ValueError):
        preview.run_directory(run_id)


def test_frozen_manifest_cannot_change_even_with_recomputed_payload_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "ROOT", tmp_path)
    run_id = "20261016T000000Z-12345678"
    directory = preview.run_directory(run_id)
    directory.mkdir(parents=True)
    cases = [{"id": f"KQ{i:02d}", "question": f"自拟问题{i}", "evidence": []} for i in range(1, 31)]
    reference = json.dumps({"cases": cases}).encode()
    rubric = b'{"author_kind":"ai"}'
    (directory / "reference-frozen.json").write_bytes(reference)
    (directory / "rubric-frozen.json").write_bytes(rubric)
    for case in cases:
        case["payload_sha256"] = fingerprint(preview.model_payload("local", case))
    manifest = {"run_id": run_id, "model": "local", "system_sha256": fingerprint(preview.SYSTEM),
                "schema_sha256": fingerprint(preview.SCHEMA), "reference_sha256": fingerprint(reference),
                "rubric_sha256": fingerprint(rubric), "cases": cases}
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    plans = tmp_path / "evidence/knowledge-update"
    plans.mkdir(parents=True)
    (plans / ("model-prevalidation-plan-"+run_id+".json")).write_text(
        json.dumps({"manifest_sha256": fingerprint(path.read_bytes())}), encoding="utf-8")
    assert preview.load_manifest(run_id)[1]["run_id"] == run_id
    manifest["cases"][0]["evidence"] = EVIDENCE
    manifest["cases"][0]["payload_sha256"] = fingerprint(preview.model_payload("local", manifest["cases"][0]))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="冻结资料"):
        preview.load_manifest(run_id)


@pytest.mark.parametrize("change", ["answer", "human"])
def test_ai_review_cannot_bind_another_answer_or_claim_human_review(change):
    original = {"case_id": "KQ01", "payload_sha256": "input", "answer": answer()}
    review = {"case_id": "KQ01", "payload_sha256": "input", "model_answer_sha256": fingerprint(original["answer"]),
              "reviewer_kind": "ai", "human_review_completed": False, "judgment": "fail",
              "reason": "自拟核查理由", "answer_excerpt": "原文写25个自然日"}
    assert preview.validate_review(original, review) == review
    if change == "answer":
        original["answer"]["answer"] = "另一次模型回答"
    else:
        review["human_review_completed"] = True
    with pytest.raises(ValueError, match="评阅身份"):
        preview.validate_review(original, review)


@pytest.mark.parametrize("excerpt", ["", "不存在于实际回答的引文"])
def test_review_example_must_be_nonempty_and_present(excerpt):
    original = {"case_id": "KQ01", "payload_sha256": "input", "answer": answer()}
    review = {"case_id": "KQ01", "payload_sha256": "input", "model_answer_sha256": fingerprint(original["answer"]),
              "reviewer_kind": "ai", "human_review_completed": False, "judgment": "fail",
              "reason": "自拟核查理由", "answer_excerpt": excerpt}
    with pytest.raises(ValueError, match="评阅举例"):
        preview.validate_review(original, review)


def test_later_runtime_summary_preserves_first_trial_observation(tmp_path):
    run_id = "20261016T000000Z-12345678"
    preview.write_summary_files(tmp_path, run_id, {"counts": {"revisions": 55}})
    first = tmp_path / ("model-prevalidation-"+run_id+".json")
    original = first.read_bytes()
    preview.write_summary_files(tmp_path, run_id, {"counts": {"revisions": 60}})
    assert first.read_bytes() == original
    assert json.loads((tmp_path / "model-prevalidation.json").read_text(encoding="utf-8"))["counts"]["revisions"] == 60
    snapshots = list(tmp_path.glob("model-prevalidation-summary-*.json"))
    assert len(snapshots) == 1 and json.loads(snapshots[0].read_text(encoding="utf-8"))["counts"]["revisions"] == 60


def test_conditions_profile_keeps_source_hints_but_not_reference_answers():
    case = {"question": "自拟问题", "evidence": EVIDENCE,
            "source_structure_notes": [{"evidence_id": "E-real", "literal_markers": []}],
            "ai_reference_answer": "GOLD_SECRET", "expected_behavior": "GOLD_BEHAVIOR"}
    payload = preview.model_payload("local", case, "conditions_v2")
    assert "GOLD_" not in json.dumps(payload, ensure_ascii=False)
    assert json.loads(payload["messages"][1]["content"])["source_structure_notes"] == case["source_structure_notes"]
    assert preview.model_payload("local", case)["messages"][0]["content"].startswith(preview.SYSTEM)


def test_distinct_quote_policy_accepts_two_quotes_of_one_source_but_not_same_pair():
    value = answer()
    value["citations"].append({"evidence_id": "E-real", "quote": "20巧年"})
    assert "duplicate_citation" in preview.validate_answer(value, EVIDENCE)
    assert preview.validate_answer(value, EVIDENCE, "distinct_quotes_v2") == []
    value["citations"].append(dict(value["citations"][0]))
    assert "duplicate_citation" in preview.validate_answer(value, EVIDENCE, "distinct_quotes_v2")


def test_unknown_prompt_and_citation_profiles_are_rejected():
    with pytest.raises(ValueError):
        preview.model_payload("local", {"question": "问题", "evidence": []}, "silent-new-version")
    with pytest.raises(ValueError):
        preview.validate_answer(answer(), EVIDENCE, "silently-weakened")

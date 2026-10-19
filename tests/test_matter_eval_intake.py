"""Intake contract fixtures only; these are not independently authored business cases."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from gov_mcp.matter_eval_intake import inspect_submission
from gov_mcp.matter_graph import canonical_hash, file_hash, load_pack
from gov_mcp.matter_native import FACTS

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/real-matter-closure/nanjing/update-snapshots-v1/registered-four"
TARGET = "20a87385efdadcecb57b3ad4f2d8e4152c0e9308"


def fixture_reference():
    pack = load_pack(PACK); citation = pack["citations"][0]
    source = next(s for s in pack["sources"] if s["source_id"] == citation["source_id"])
    segment = next(s for s in source["segments"] if s["segment_id"] == citation["segment_id"])
    return {"schema": 1, "status": "SUBMITTED_FOR_REFERENCE_REVIEW", "reviewer": "explicit-contract-fixture",
        "independence_declaration": "Fixture declaration only; not a real independent reviewer.",
        "reference_frozen_at": None, "predictions_executed": 0, "contains_real_applicant_identifiers": False,
        "source_package_sha256": file_hash((PACK / "manifest.json").read_bytes()), "implementation_commit": TARGET,
        "human_business_review": False, "formal_publication": False,
        "case_groups": [{"group_id": "FIXTURE-01", "origin_of_real_question": "Explicit contract fixture, no actual business evaluation",
            "question": "这是一条专用于参考接入契约验证的夹具问题，不是独立业务样本。", "variants": [],
            "topic": "materials", "declared_conditions": deepcopy(FACTS),
            "development_overlap_disclosure": "Fixture only; semantic independence unreviewed",
            "expected_answer_or_stop": "Fixture expectation, not an approved policy answer",
            "reference_reasoning": "Fixture citation binding check only", "official_reference_urls": [source["url"]],
            "exact_reference_locators": [{"source_id": source["source_id"], "segment_id": segment["segment_id"],
                "locator": segment["locator"], "quote": citation["quote"], "original_sha256": source["original_sha256"]}]}]}


def codes(report): return {v["code"] for v in report["issues"]}


def test_empty_real_template_is_not_frozen_evaluated_or_ready():
    template = json.loads((ROOT / "evaluation/real-matter-closure/independent-submission.template.json").read_text(encoding="utf-8"))
    result = inspect_submission(template, PACK, TARGET)
    assert result["status"] == "REFERENCE_NOT_READY" and result["case_group_count"] == 0
    assert {"EXTERNAL_REVIEWER_REQUIRED", "EXTERNAL_CASE_GROUPS_REQUIRED", "SOURCE_PACKAGE_BINDING_REQUIRED"} <= codes(result)
    assert not result["independence_certified"] and not result["reference_frozen"] and result["predictions_executed"] == 0


def test_bound_fixture_only_admits_technical_review_without_certifying_author_or_answer():
    reference = fixture_reference(); before = deepcopy(reference)
    result = inspect_submission(reference, PACK, TARGET)
    assert not result["issues"] and result["bound_anchor_count"] == 1 and reference == before
    assert result["status"] == "TECHNICAL_BINDING_READY_ATTRIBUTION_AND_FREEZE_STILL_REQUIRED"
    for key in ("reference_frozen", "independence_certified", "human_attribution_verified", "semantic_reference_review_completed", "can_execute_models", "formal_publication"):
        assert result[key] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert reference["case_groups"][0]["question"] not in serialized
    assert reference["reviewer"] not in serialized


@pytest.mark.parametrize(("field", "value", "code"), [
    ("quote", "原件没有这句文字", "OFFICIAL_ANCHOR_BINDING_FAILED"),
    ("original_sha256", "0" * 64, "OFFICIAL_ANCHOR_BINDING_FAILED"),
    ("locator", "不存在的原文定位", "OFFICIAL_ANCHOR_BINDING_FAILED"),
])
def test_changed_quote_original_or_locator_cannot_pass(field, value, code):
    reference = fixture_reference(); reference["case_groups"][0]["exact_reference_locators"][0][field] = value
    assert code in codes(inspect_submission(reference, PACK, TARGET))


def test_development_overlap_duplicate_groups_and_private_text_are_rejected_without_echo():
    reference = fixture_reference(); group = reference["case_groups"][0]
    group["question"] = load_pack(PACK)["development_tasks"][0]["question"]
    other = deepcopy(group); other["group_id"] = "FIXTURE-02"; other["question"] = " \n" + group["question"]
    reference["case_groups"].append(other)
    result = inspect_submission(reference, PACK, TARGET)
    assert {"EXACT_DEVELOPMENT_QUESTION_OVERLAP", "DUPLICATE_NORMALIZED_QUESTION"} <= codes(result)
    group["question"] = "仅用于隐私拒绝测试的模拟号码123456789012345678，不应进入输出。"
    result = inspect_submission(reference, PACK, TARGET)
    assert "POSSIBLE_PERSONAL_DATA" in codes(result) and "123456789012345678" not in json.dumps(result)


def test_post_prediction_or_different_commit_cannot_be_admitted():
    reference = fixture_reference(); reference.update(predictions_executed=1, implementation_commit="f" * 40)
    assert {"PREDICTIONS_ALREADY_STARTED_OR_UNDECLARED", "TARGET_IMPLEMENTATION_COMMIT_MISMATCH"} <= codes(inspect_submission(reference, PACK, TARGET))


def test_scope_boundary_reference_is_separate_from_official_policy_anchor():
    reference = fixture_reference(); group = reference["case_groups"][0]
    group.update(reference_basis="declared_scope_boundary", scope_sha256=canonical_hash(load_pack(PACK)["scope"]),
        official_reference_urls=[], exact_reference_locators=[], declared_conditions={"adult": None})
    result = inspect_submission(reference, PACK, TARGET)
    assert not result["issues"] and result["bound_anchor_count"] == 0 and not result["semantic_reference_review_completed"]
    group["scope_sha256"] = "0" * 64
    assert "DECLARED_SCOPE_REFERENCE_BINDING_REQUIRED" in codes(inspect_submission(reference, PACK, TARGET))


def test_malformed_types_are_blocked_and_tampered_pack_cannot_supply_oracle(tmp_path):
    reference = fixture_reference(); group = reference["case_groups"][0]
    group.update(topic=[], declared_conditions={"adult": "true"})
    assert {"SUPPORTED_UI_TOPIC_REQUIRED", "ANONYMOUS_CONDITION_SCHEMA_REQUIRED"} <= codes(inspect_submission(reference, PACK, TARGET))
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert "SOURCE_PACKAGE_INTEGRITY_FAILED" in codes(inspect_submission(reference, tmp_path, TARGET))

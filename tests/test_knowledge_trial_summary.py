from finalize_knowledge_intake import model_trial_summary


def bound_records():
    trial = {"run_id": "new-run", "reference_sha256": "ref", "rubric_sha256": "rubric",
             "case_count": 30, "completed_cases": 30, "technical_pass_count": 24}
    review = {"run_id": "new-run", "frozen_reference_sha256": "ref", "frozen_rubric_sha256": "rubric",
              "reviewed_cases": 30, "ai_semantic_pass_count": 9, "business_release_ready": True}
    return trial, review


def test_no_trial_or_review_does_not_look_like_completed_review():
    result = model_trial_summary({}, {})
    assert result["review_matches_complete_frozen_run"] is False
    assert result["ai_semantic_pass_count"] is None


def test_old_run_review_is_not_attached_to_new_answers():
    trial, review = bound_records()
    review["run_id"] = "old-run"
    result = model_trial_summary(trial, review)
    assert result["review_run_id_observed"] == "old-run"
    assert result["ai_semantic_pass_count"] is None


def test_same_run_with_different_standard_is_not_attached():
    trial, review = bound_records()
    review["frozen_rubric_sha256"] = "other-rubric"
    assert not model_trial_summary(trial, review)["review_matches_complete_frozen_run"]


def test_partial_model_run_is_not_scored_as_complete_review():
    trial, review = bound_records()
    trial["completed_cases"] = 20
    assert model_trial_summary(trial, review)["ai_semantic_pass_count"] is None


def test_complete_matching_ai_review_preserves_results_without_release_claim():
    result = model_trial_summary(*bound_records())
    assert result["review_matches_complete_frozen_run"] is True
    assert result["ai_semantic_pass_count"] == 9
    assert result["technical_pass_count"] == 24
    assert result["business_release_ready"] is False
    assert result["independent_human_acceptance"] is False

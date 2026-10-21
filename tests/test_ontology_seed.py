"""Candidate grammar and provenance checks; fixtures are not business approval."""

from copy import deepcopy
import hashlib

import pytest

from gov_mcp.knowledge_landing import URLS
from gov_mcp.ontology_seed import build_candidate


HEADER = "提示：本市户籍居民符合以下条件可在网上办理居民身份证损坏换领业务："


def evidence(age=16, card=5, photo=5, fingerprint="一"):
    continuation = (f"本市户籍居民，{age}周岁以上，{card}年之内办理过居民身份证，"
                    f"{photo}年之内采集过居民身份证照片，至少有{fingerprint}枚指纹注册成功。")
    pins = {"source_id": "BJ-ID-RENEWAL-2025", "url": URLS["BJ-ID-RENEWAL-2025"],
            "revision_id": "rev-engineering-fixture", "original_sha256": "a" * 64}
    clauses = [{"locator": "正文段落24", "text": HEADER, "category": "identity"},
               {"locator": "正文段落25", "text": continuation, "category": "identity"}]
    return {"mode": "public_candidate_preview", "status": "SOURCE_EXCERPTS",
            "formally_published": False, "business_conclusion_status": "PAUSED_UNREVIEWED",
            "region": "北京市", "matter": "resident-id-renewal",
            "source_context": [{**pins, "selected_clauses": clauses}],
            # The ranked top five can omit the condition continuation. The same
            # verified context must retain it and supply its own full citation.
            "citations": [{**pins, "locator": clauses[0]["locator"], "quote": HEADER,
                           "text_sha256": hashlib.sha256(HEADER.encode()).hexdigest()}]}


def condition_values(candidate):
    return {condition["field"]: condition["value"] for condition in candidate["conditions"]}


def test_candidate_preserves_both_source_clauses_without_mutation():
    result = evidence()
    before = deepcopy(result)
    candidate = build_candidate(result)
    assert result == before
    assert candidate["entity_type"]["name"] == "ServiceChannel"
    assert candidate["relation_type"] == {"name": "available_via", "domain": "Matter",
                                          "range": "ServiceChannel", "operator": "all"}
    assert candidate["scope"] == {"region": "北京市", "matter": "resident-id-renewal", "branch": "damaged"}
    assert candidate["channel"] == "online"
    assert condition_values(candidate) == {"local_household": True, "age_years": 16,
                                           "card_years": 5, "photo_years": 5, "fingerprint_count": 1}
    assert [citation["quote"] for citation in candidate["citations"]] == [
        clause["text"] for clause in result["source_context"][0]["selected_clauses"]]
    assert all(citation["text_sha256"] == hashlib.sha256(citation["quote"].encode()).hexdigest()
               for citation in candidate["citations"])
    assert candidate["status"] == "pending_review"
    assert candidate["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert candidate["current_applicability"] == "unknown"
    assert candidate["formal_approval"] is candidate["formally_published"] is False
    assert candidate["ambiguous_boundaries"] == ["age_years", "card_years", "photo_years"]


def test_thresholds_come_from_text_instead_of_expected_answer_lookup():
    candidate = build_candidate(evidence(age=18, card=3, photo=2, fingerprint="两"))
    assert condition_values(candidate) == {"local_household": True, "age_years": 18,
                                           "card_years": 3, "photo_years": 2, "fingerprint_count": 2}
    assert candidate["construction_metrics"]["cloud_calls"] == 0


@pytest.mark.parametrize("replacement", [
    "本市户籍居民，16周岁以上，5年之内办理过居民身份证。",
    "本市户籍居民，16周岁以上，5年之内办理过居民身份证，5年之内未采集过居民身份证照片，至少有一枚指纹注册成功。",
    "本市户籍居民，16周岁以上，5年之内办理过居民身份证，5年之内采集过居民身份证照片，至少有一枚指纹注册成功，另需现场确认。",
    "本市户籍居民，16周岁以下，5年之内办理过居民身份证，5年之内采集过居民身份证照片，至少有一枚指纹注册成功。",
])
def test_missing_negated_or_extra_conditions_are_never_dropped(replacement):
    result = evidence()
    result["source_context"][0]["selected_clauses"][1]["text"] = replacement
    with pytest.raises(ValueError, match="UNSUPPORTED_OR_INCOMPLETE"):
        build_candidate(result)


@pytest.mark.parametrize("change", ["missing", "nonadjacent", "reordered", "duplicate"])
def test_missing_or_nonadjacent_continuation_fails_closed(change):
    result = evidence()
    clauses = result["source_context"][0]["selected_clauses"]
    if change == "missing":
        clauses.pop()
    elif change == "nonadjacent":
        clauses[1]["locator"] = "正文段落26"
    elif change == "reordered":
        clauses.reverse()
    else:
        clauses.extend([{**clauses[0], "locator": "正文段落26"},
                        {**clauses[1], "locator": "正文段落27"}])
    with pytest.raises(ValueError):
        build_candidate(result)


@pytest.mark.parametrize("change", ["source", "url", "revision", "raw_hash", "quote", "text_hash", "no_citations", "scope"])
def test_provenance_mismatch_or_missing_anchor_blocks_candidate(change):
    result = evidence()
    citation = result["citations"][0]
    if change == "source":
        citation["source_id"] = "NJ-ID-RENEWAL-CYCLE-2026"
    elif change == "url":
        citation["url"] = "https://example.invalid/"
    elif change == "revision":
        citation["revision_id"] = "rev-other"
    elif change == "raw_hash":
        citation["original_sha256"] = "b" * 64
    elif change == "quote":
        citation["quote"] += "changed"
    elif change == "text_hash":
        citation["text_sha256"] = "b" * 64
    elif change == "no_citations":
        result["citations"] = []
    else:
        result["region"] = "南京市"
    with pytest.raises(ValueError):
        build_candidate(result)


def test_other_branch_or_negated_channel_header_is_not_reinterpreted():
    for header in (HEADER.replace("损坏", "到期"), HEADER.replace("可在", "不可在")):
        result = evidence()
        result["source_context"][0]["selected_clauses"][0]["text"] = header
        result["citations"][0].update(quote=header, text_sha256=hashlib.sha256(header.encode()).hexdigest())
        with pytest.raises(ValueError, match="ONE_SUPPORTED_CHANNEL_HEADER"):
            build_candidate(result)


def test_additional_condition_paragraph_is_not_silently_ignored():
    result = evidence()
    result["source_context"][0]["selected_clauses"].append(
        {"locator": "正文段落26", "category": "identity", "text": "另需现场确认。"})
    with pytest.raises(ValueError, match="CHANNEL_BLOCK_MUST_END"):
        build_candidate(result)

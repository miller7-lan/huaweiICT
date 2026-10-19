"""Real-source section routing and scope boundaries, not independent accuracy scoring."""
from datetime import datetime
from pathlib import Path

import pytest

from gov_mcp.matter_graph import load_pack, MatterProofError
from gov_mcp.matter_guidance import guidance, TOPICS
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
    "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu"}
NOW = datetime.fromisoformat("2026-10-19T13:00:00+00:00").timestamp()


@pytest.fixture
def pack():
    return load_pack(PACK)


@pytest.mark.parametrize("topic", [t for t in TOPICS if t != "old_card_lifecycle"])
def test_each_consultation_topic_returns_bound_source_sections(pack, topic):
    result = guidance(pack, FACTS, topic, now=NOW)
    assert result["status"] == "SOURCE_GUIDANCE_PENDING_REVIEW"
    assert {s["rule_id"] for s in result["sections"]} == set(TOPICS[topic]["rules"])
    assert not result["knowledge_published"] and not result["formal_business_answer"]
    for section in result["sections"]:
        for citation in section["citations"]:
            assert citation["full_context"][citation["start"]:citation["end"]] == citation["quote"]
            assert citation["quote"] == section["source_statement"]


def test_overview_covers_all_sixteen_and_does_not_assume_old_card_validity(pack):
    result = guidance(pack, FACTS, "overview", now=NOW)
    assert len(result["sections"]) == 16
    old = next(s for s in result["sections"] if s["rule_id"] == "NR12")
    assert old["applicability"] == "needs_validity_clarification"


def test_material_exceptions_are_not_accumulated_into_main_branch(pack):
    result = guidance(pack, FACTS, "materials", now=NOW)
    assert [s["applicability"] for s in result["sections"]] == [
        "source_description", "exception_context", "exception_context"]


def test_online_exclusion_keeps_full_positive_and_negative_context(pack):
    result = guidance(pack, FACTS, "online", now=NOW)
    text = result["sections"][1]["citations"][0]["full_context"]
    assert "江苏户籍居民符合以下条件之一" in text
    assert "证件有效期满换领" in text and "不予“全程网办”" in text


def test_fee_relief_is_not_determined_from_standard_fee(pack):
    result = guidance(pack, {**FACTS, "fee_relief_requested": True}, "fee", now=NOW)
    assert result["sections"][0]["applicability"] == "fee_relief_requires_separate_review"
    assert "困难减免资格和证明" in result["not_decided"]


def test_service_days_and_statutory_days_have_distinct_rules_and_no_exact_date(pack):
    result = guidance(pack, {**FACTS, "urgent_exam": True}, "time", now=NOW)
    assert [s["rule_id"] for s in result["sections"]] == ["NR08", "NR09", "NR14"]
    assert "20个工作日" in result["sections"][0]["source_statement"]
    assert "六十日内" in result["sections"][2]["source_statement"]
    assert "考生加急具体天数" in result["not_decided"]


def test_expired_old_card_is_marked_not_applicable_in_collection_topic(pack):
    result = guidance(pack, {**FACTS, "old_card_valid": False}, "collection", now=NOW)
    assert next(s for s in result["sections"] if s["rule_id"] == "NR12")["applicability"] == "condition_not_met"


def test_2025_to_2028_window_not_unconditionally_extended(pack):
    later = datetime.fromisoformat("2029-01-01T00:00:00+08:00").timestamp()
    result = guidance(pack, FACTS, "window", now=later)
    assert result["sections"][1]["applicability"] == "source_service_period_outside_current_date"


def test_future_published_source_is_not_used_as_current_knowledge(pack):
    earlier = datetime.fromisoformat("2025-06-01T00:00:00+08:00").timestamp()
    with pytest.raises(MatterProofError, match="公布日期"):
        guidance(pack, FACTS, "time", now=earlier)


@pytest.mark.parametrize("change,status", [
    ({"civil_capacity": None}, "NEEDS_CLARIFICATION"),
    ({"civil_capacity": "limited"}, "OUT_OF_SCOPE"),
    ({"household": "outside_jiangsu"}, "OUT_OF_SCOPE"),
])
def test_incomplete_or_other_branch_does_not_reuse_main_case(pack, change, status):
    assert guidance(pack, {**FACTS, **change}, "materials", now=NOW)["status"] == status


def test_topic_survives_case_handoff_and_pins_the_same_version(tmp_path):
    app = MatterLifecycle(tmp_path / "runtime", clock=lambda: NOW)
    maint = Principal("fixture-maint", "maintainer", "automated_test")
    citizen = Principal("fixture-citizen", "citizen", "automated_test")
    staff = Principal("fixture-staff", "staff", "automated_test")
    candidate = app.prepare(PACK, ontology_stage="evolved", principal=maint)
    version = app.release_preview(candidate["candidate_id"], None, note="fixture", principal=maint)["version"]
    case = app.open_case(FACTS, principal=citizen, topic="materials")
    code = app.handoff(case["case_id"], principal=citizen)["handoff_token"]
    received = app.accept(case["case_id"], code, principal=staff)
    assert received["topic"] == "materials" and received["version"] == version
    assert [s["rule_id"] for s in received["sections"]] == ["NR05", "NR06", "NR07"]

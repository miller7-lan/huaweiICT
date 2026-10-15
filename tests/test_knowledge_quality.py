"""Fabricated reports below are unit fixtures only, never human business evaluation evidence."""

import copy
import json
import time
from datetime import datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore, encode


@pytest.fixture
def evaluated(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "strict")
    config = {"id": "synthetic", "label": "纯工程夹具", "origin": "https://fixtures.invalid",
        "enabled": True, "allowed_paths": ["/doc"], "seeds": ["https://fixtures.invalid/doc"],
        "interval_seconds": 3600, "delay_seconds": 5,
        "metadata": {"title": "模拟文件", "region": "controlled", "matter": "fixture", "document_type": "synthetic",
                     "effective_from": "2026-01-01", "effective_to": None, "usage_basis": "self-authored engineering fixture"},
        "access_review": {"approved": True, "reviewer": "synthetic-no-real-person", "actor_kind": "synthetic",
                          "usage_basis": "Self-authored fixture; zero network", "checked_at": datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    revision = store.ingest("synthetic", config["seeds"][0], b"synthetic-only", config["metadata"],
        [{"locator": name, "category": "procedure", "text": "模拟办理流程原文"+name} for name in ("a", "b")])
    store.review(revision, "synthetic-no-real-person", True)
    version = store.candidate("controlled", "fixture")
    with store.db() as db:
        db.execute("UPDATE sites SET last_success=?", (time.time(),))
    refs = [{"revision": revision, "locator": loc} for loc in ("a", "b")]
    cases = []
    for i in range(30):
        answer = i < 29
        cases.append({"id": str(i), "question": "纯模拟问题"+str(i), "human_judgment": "pass",
            "reference_answer": "纯模拟标准答案，不存在真人签字", "observed_answer": "纯模拟被测输出",
            "expected_outcome": "answer" if answer else "stop", "observed_outcome": "answer" if answer else "stop",
            "unsupported_claims": 0, "wrong_region_claims": 0, "expired_rules_as_current": 0,
            "missing_critical_requirements": 0, "claim_count": 1 if answer else 0, "supported_claim_count": 1 if answer else 0,
            "relevant_evidence": copy.deepcopy(refs) if answer else [], "retrieved_top5": copy.deepcopy(refs) if answer else []})
    report = {"version": version, "snapshot_binding": store.evaluation_binding(version), "status": "human_reviewed",
              "reference_author_kind": "human", "reference_reviewer": "SYNTHETIC_FIELD_NOT_A_REAL_REVIEWER",
              "reference_reviewed_at": datetime.now(timezone.utc).isoformat(),
              "dataset_role": "independent_business_acceptance", "cases": cases,
              "fixture_warning": "Fields emulate a report schema for testing only. No human business review occurred."}
    return store, config, version, report


def test_strict_default_stops_publication_without_evaluation(evaluated):
    store, _, version, _ = evaluated
    with pytest.raises(ValueError, match="business evaluation required"):
        store.publish(version, "synthetic-test", True)
    assert not store.status()["publications"]


def test_snapshot_bound_report_counts_real_denominators_and_enables_gate(evaluated):
    store, _, version, report = evaluated
    result = store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    assert result["metrics"]["case_count"] == 30
    assert result["metrics"]["recall_denominator"] == 29
    assert result["metrics"]["claim_count"] == 29
    assert store.publication_check(version)["mode"] == "business"
    store.publish(version, "SYNTHETIC_TEST_ONLY", True)


@pytest.mark.parametrize("kind", ["draft", "too_few", "duplicate_question", "unknown_citation", "missing_answer",
                                  "unsupported", "expired", "wrong_region", "missing_material", "all_stop", "low_recall", "wrong_binding"])
def test_invalid_report_cannot_pass(evaluated, kind):
    store, _, version, report = evaluated
    if kind == "draft":
        report["status"] = "draft_unreviewed"
    elif kind == "too_few":
        report["cases"].pop()
    elif kind == "duplicate_question":
        report["cases"][1]["question"] = report["cases"][0]["question"]
    elif kind == "unknown_citation":
        report["cases"][0]["retrieved_top5"][0]["locator"] = "does-not-exist"
    elif kind == "missing_answer":
        report["cases"][0]["reference_answer"] = None
    elif kind in ("unsupported", "expired", "wrong_region", "missing_material"):
        key = {"unsupported": "unsupported_claims", "expired": "expired_rules_as_current", "wrong_region": "wrong_region_claims", "missing_material": "missing_critical_requirements"}[kind]
        report["cases"][0][key] = 1
    elif kind == "all_stop":
        for case in report["cases"]:
            case.update(expected_outcome="stop", observed_outcome="stop", claim_count=0, supported_claim_count=0,
                        relevant_evidence=[], retrieved_top5=[])
    elif kind == "low_recall":
        for case in report["cases"][:-1]:
            case["retrieved_top5"].pop()
    else:
        report["snapshot_binding"] = "wrong"
    with pytest.raises(ValueError):
        store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    assert not store.status()["publications"]


def test_changed_sources_and_report_tampering_invalidate_old_evaluation(evaluated):
    store, config, version, report = evaluated
    recorded = store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    altered = copy.deepcopy(config)
    altered["label"] += "changed"
    store.register(altered)
    with pytest.raises(ValueError, match="bind"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)
    store.register(config)
    with store.db() as db:
        db.execute("UPDATE snapshot_evaluations SET report='{}' WHERE id=?", (recorded["evaluation"],))
    with pytest.raises(ValueError, match="fingerprint"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)


def test_paused_or_stale_source_stops_approved_publication(evaluated):
    store, _, version, report = evaluated
    store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    with store.db() as db:
        db.execute("UPDATE sites SET paused_reason='access denied'")
    with pytest.raises(ValueError, match="recently checked"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)


def test_recent_success_does_not_allow_publication_after_a_newer_failure(evaluated):
    store, _, version, report = evaluated
    store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    with store.db() as db:
        db.execute("UPDATE sites SET failures=1")
    with pytest.raises(ValueError, match="recently checked"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)
    assert not store.status()["publications"]


@pytest.mark.parametrize("broken_policy", ["missing", "weakened"])
def test_missing_or_weakened_policy_cannot_disable_quality_gate(evaluated, broken_policy):
    store, _, version, _ = evaluated
    path = store.root / "publication-policy.json"
    if broken_policy == "missing":
        path.unlink()
    else:
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["minimum_cases"] = 1
        path.write_text(encode(policy), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)
    assert not store.status()["publications"]


def test_fixture_policy_refuses_actual_document_types(evaluated):
    store, config, version, _ = evaluated
    (store.root / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    metadata = dict(config["metadata"], document_type="service_guide")
    with store.db() as db:
        member = json.loads(db.execute("SELECT members FROM versions WHERE id=?", (version,)).fetchone()[0])[0]
    revision = store.amend(member, metadata)
    store.review(revision, "SYNTHETIC_TEST_ONLY", True)
    version = store.candidate("controlled", "fixture")
    with pytest.raises(ValueError, match="refuses real"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)


def test_added_impact_monitor_invalidates_evaluation_and_must_be_checked(evaluated):
    store, config, version, report = evaluated
    store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    monitor = copy.deepcopy(config)
    monitor.update(id="synthetic-monitor", watch_scopes=[{"region": "controlled", "matter": "fixture"}])
    store.register(monitor)
    with pytest.raises(ValueError, match="bind"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)
    report["snapshot_binding"] = store.evaluation_binding(version)
    store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    with pytest.raises(ValueError, match="recently checked"):
        store.publish(version, "SYNTHETIC_TEST_ONLY", True)

"""Fabricated reports below are unit fixtures only, never human business evaluation evidence."""

import copy
import json
import time
from datetime import datetime, timezone, timedelta

import pytest

from gov_mcp.knowledge import KnowledgeStore, encode, fingerprint
from gov_mcp.knowledge_quality import BUSINESS_POLICY, ATTRIBUTED_BUSINESS_POLICY, publication_policy
from gov_mcp.knowledge_review import (development_registry, register_development_questions, upgrade_review_contract,
    question_fingerprint, reference_fingerprint, observation_fingerprint)


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


def rebind_artifacts(report):
    report["reference_sha256"] = reference_fingerprint(report)
    report["observation_sha256"] = observation_fingerprint(report)


@pytest.fixture
def attributed(evaluated):
    """Every actor/output/trace is synthetic; this does not run or attest a real model."""
    store, config, version, legacy = evaluated
    registered = register_development_questions(store, ["模拟开发题"], "self-authored synthetic development fixture")
    upgrade_review_contract(store, fingerprint(BUSINESS_POLICY), registered["registry_sha256"], "synthetic maintainer", True)
    actor = {"kind": "ai", "name": "synthetic AI reviewer", "model": "synthetic-no-model-executed",
             "model_revision": None, "run_id": "synthetic-review-run"}
    now = datetime.now(timezone.utc)
    report = {"schema": 2, "version": version, "snapshot_binding": store.evaluation_binding(version),
        "status": "reviewed", "dataset_role": "independent_business_acceptance",
        "reference_author": dict(actor, name="synthetic reference author"), "reviewer": actor,
        "human_review_completed": False,
        "reference_frozen_at": (now-timedelta(hours=4)).isoformat(),
        "reference_reviewed_at": (now-timedelta(hours=1)).isoformat(),
        "observed_run": {"id": "synthetic-observation-run", "executor": dict(actor, name="synthetic tested model"),
            "started_at": (now-timedelta(hours=3)).isoformat(), "completed_at": (now-timedelta(hours=2)).isoformat(),
            "reference_answers_in_model_input": False},
        "holdout": {"method": "normalized_question_hash_disjoint",
            "development_question_sha256": [question_fingerprint("模拟开发题")], "registry_sha256": registered["registry_sha256"]},
        "cases": copy.deepcopy(legacy["cases"]),
        "fixture_warning": "All identities, traces and outputs are self-authored synthetic fields. No human or AI business evaluation occurred."}
    for case in report["cases"]:
        case.pop("human_judgment")
        case.update(review_judgment="pass", review_notes="Synthetic schema exercise; not actual policy truth.",
            trace_sha256=fingerprint(("synthetic trace "+case["id"]).encode()),
            input_sha256=fingerprint(("synthetic input "+case["id"]).encode()))
    rebind_artifacts(report)
    return store, config, version, report


@pytest.mark.parametrize("author_kind,reviewer_kind", [("ai", "ai"), ("human", "ai"), ("ai", "human"), ("human", "human")])
def test_attributed_actors_pass_same_quality_gate_with_truthful_metrics(attributed, author_kind, reviewer_kind):
    store, _, version, report = attributed
    for field, kind in (("reference_author", author_kind), ("reviewer", reviewer_kind)):
        if kind == "human":
            report[field] = {"kind": "human", "name": "SYNTHETIC_SCHEMA_PERSON_NO_REAL_SIGNATURE"}
    report["human_review_completed"] = reviewer_kind == "human"
    rebind_artifacts(report)
    recorded = store.record_evaluation(version, report, report["reviewer"]["name"], True)
    metrics = recorded["metrics"]
    assert (metrics["case_count"], metrics["recall_denominator"], metrics["claim_count"]) == (30, 29, 29)
    assert metrics["mean_recall_at_5"] == metrics["claim_support_rate"] == 1
    assert metrics["reference_author_kind"] == author_kind
    assert metrics["reviewer_kind"] == reviewer_kind
    assert metrics["human_review_completed"] is (reviewer_kind == "human")
    assert store.publication_check(version)["metrics"]["review_contract"] == "attributed_v2"
    store.publish(version, report["reviewer"]["name"], True)


@pytest.mark.parametrize("broken", ["unknown_actor", "missing_model", "missing_model_revision", "false_human",
    "legacy_case", "legacy_root", "gold_leak", "late_freeze", "reversed_run", "future_review", "naive_time",
    "missing_trace", "missing_input", "wrong_reference_hash", "changed_reference", "changed_output",
    "development_overlap", "normalized_duplicate", "omitted_registry", "invented_registry", "missing_notes",
    "failed_judgment", "development_role", "draft_status", "wrong_evaluator"])
def test_attributed_provenance_cannot_be_bypassed(attributed, broken):
    store, _, version, report = attributed
    if broken == "unknown_actor": report["reviewer"]["kind"] = "machine"
    elif broken == "missing_model": report["reviewer"].pop("model")
    elif broken == "missing_model_revision": report["reviewer"].pop("model_revision")
    elif broken == "false_human": report["human_review_completed"] = True
    elif broken == "legacy_case": report["cases"][0]["human_judgment"] = "pass"
    elif broken == "legacy_root": report["reference_author_kind"] = "human"
    elif broken == "gold_leak": report["observed_run"]["reference_answers_in_model_input"] = True
    elif broken == "late_freeze": report["reference_frozen_at"] = report["observed_run"]["completed_at"]
    elif broken == "reversed_run": report["observed_run"]["completed_at"] = report["reference_frozen_at"]
    elif broken == "future_review": report["reference_reviewed_at"] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    elif broken == "naive_time": report["reference_frozen_at"] = "2026-01-01T00:00:00"
    elif broken == "missing_trace": report["cases"][0]["trace_sha256"] = "0"*64
    elif broken == "missing_input": report["cases"][0]["input_sha256"] = None
    elif broken == "wrong_reference_hash": report["reference_sha256"] = fingerprint(b"wrong")
    elif broken == "changed_reference": report["cases"][0]["reference_answer"] += " changed"
    elif broken == "changed_output": report["cases"][0]["observed_answer"] += " changed"
    elif broken == "development_overlap": report["cases"][0]["question"] = " 模拟\n开发题 "
    elif broken == "normalized_duplicate": report["cases"][1]["question"] = " "+report["cases"][0]["question"]+"\n"
    elif broken in ("omitted_registry", "invented_registry"):
        values = [] if broken == "omitted_registry" else [question_fingerprint("unregistered")]
        report["holdout"].update(development_question_sha256=values, registry_sha256=fingerprint(values))
    elif broken == "missing_notes": report["cases"][0]["review_notes"] = ""
    elif broken == "failed_judgment": report["cases"][0]["review_judgment"] = "fail"
    elif broken == "development_role": report["dataset_role"] = "development_prevalidation_not_independent_acceptance"
    elif broken == "draft_status": report["status"] = "draft"
    # Rebind semantic-invalid examples so failure cannot be explained by a stale hash alone.
    if broken not in ("wrong_reference_hash", "changed_reference", "changed_output"):
        rebind_artifacts(report)
    reviewer = "different recorder" if broken == "wrong_evaluator" else report["reviewer"]["name"]
    with pytest.raises(ValueError):
        store.record_evaluation(version, report, reviewer, True)
    assert not store.status()["publications"]


@pytest.mark.parametrize("broken", ["too_few", "unsupported_claims", "wrong_region_claims", "expired_rules_as_current",
    "missing_critical_requirements", "low_recall", "claim_support", "wrong_outcome"])
def test_attributed_actor_support_keeps_quality_thresholds(attributed, broken):
    store, _, version, report = attributed
    if broken == "too_few": report["cases"].pop()
    elif broken == "low_recall":
        for case in report["cases"][:-1]:case["retrieved_top5"].pop()
    elif broken == "claim_support": report["cases"][0]["supported_claim_count"] = 0
    elif broken == "wrong_outcome": report["cases"][0]["observed_outcome"] = "stop"
    else: report["cases"][0][broken] = 1
    rebind_artifacts(report)
    with pytest.raises(ValueError):store.record_evaluation(version, report, report["reviewer"]["name"], True)
    assert not store.status()["publications"]


def test_registered_development_changes_invalidate_prior_attributed_evaluation(attributed):
    store, _, version, report = attributed
    store.record_evaluation(version, report, report["reviewer"]["name"], True)
    register_development_questions(store, ["另一个开发问题"], "synthetic-new-development")
    with pytest.raises(ValueError, match="bind"):
        store.publish(version, report["reviewer"]["name"], True)


def test_contract_upgrade_binds_inspected_policy_and_retains_legacy(evaluated):
    store, _, version, report = evaluated
    before = store.evaluation_binding(version)
    with pytest.raises(ValueError, match="changed"):
        upgrade_review_contract(store, "wrong", fingerprint([]), "synthetic", True)
    assert publication_policy(store) == BUSINESS_POLICY
    change = upgrade_review_contract(store, fingerprint(BUSINESS_POLICY), fingerprint([]), "synthetic", True)
    assert change["quality_thresholds_unchanged"] and not change["publication_performed"]
    assert json.loads((store.root/"policy-history"/(fingerprint(BUSINESS_POLICY)+".json")).read_text()) == BUSINESS_POLICY
    assert before != store.evaluation_binding(version)
    report["snapshot_binding"] = store.evaluation_binding(version)
    with pytest.raises(ValueError, match="Attributed"):
        store.record_evaluation(version, report, "synthetic", True)
    assert upgrade_review_contract(store, fingerprint(ATTRIBUTED_BUSINESS_POLICY), fingerprint([]), "synthetic", True)["changed"] is False


def test_upgrade_does_not_change_active_legacy_publication(evaluated):
    store, _, version, report = evaluated
    store.record_evaluation(version, report, "SYNTHETIC_TEST_ONLY", True)
    store.publish(version, "synthetic", True)
    with pytest.raises(ValueError, match="no live"):
        upgrade_review_contract(store, fingerprint(BUSINESS_POLICY), fingerprint([]), "synthetic", True)
    assert publication_policy(store) == BUSINESS_POLICY


def test_read_only_and_unconfirmed_contract_operations_do_not_write(evaluated):
    store, _, _, _ = evaluated
    reader = KnowledgeStore(store.root, read_only=True)
    for target, confirmed in ((reader, True), (store, False)):
        with pytest.raises(ValueError, match="confirmation"):
            upgrade_review_contract(target, fingerprint(BUSINESS_POLICY), fingerprint([]), "synthetic", confirmed)
    with pytest.raises(ValueError, match="Writable"):
        register_development_questions(reader, ["question"], "synthetic")
    assert publication_policy(store) == BUSINESS_POLICY


def test_attributed_registry_and_report_survive_full_bundle_restore(attributed, tmp_path):
    from gov_mcp.knowledge_backup import create_bundle, restore_bundle
    store, _, version, report = attributed
    store.record_evaluation(version, report, report["reviewer"]["name"], True)
    create_bundle(store, tmp_path/"attributed-backup")
    restore_bundle(tmp_path/"attributed-backup", tmp_path/"attributed-restored")
    restored = KnowledgeStore(tmp_path/"attributed-restored", read_only=True)
    assert publication_policy(restored) == ATTRIBUTED_BUSINESS_POLICY
    assert restored.evaluation_binding(version) == store.evaluation_binding(version)
    with restored.db() as db:
        assert development_registry(db) == report["holdout"]["development_question_sha256"]
    assert restored.publication_check(version)["metrics"]["reviewer_kind"] == "ai"


def test_policy_upgrade_serializes_against_concurrent_publication(evaluated, monkeypatch):
    import sqlite3
    from pathlib import Path
    store, _, _, _ = evaluated
    replace = Path.replace
    locked = []
    def race_at_policy_replace(path, target):
        with sqlite3.connect(store.path, timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
            locked.append(True)
        return replace(path, target)
    monkeypatch.setattr(Path, "replace", race_at_policy_replace)
    upgrade_review_contract(store, fingerprint(BUSINESS_POLICY), fingerprint([]), "synthetic", True)
    assert locked == [True]
    assert not store.status()["publications"]

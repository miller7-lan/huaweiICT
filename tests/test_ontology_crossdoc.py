"""Synthetic integration checks; no network, native run or policy activation.

The existing source fixtures are deliberately synthetic. Their successful use
attests the reviewed execution contract, never current business applicability.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from gov_mcp import ontology_crossdoc as integration
from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS
from test_crossdoc_reasoning import archive, documents  # noqa: F401 -- reusable synthetic fixtures


ACTOR = {"kind": "human", "name": "Synthetic integration reviewer"}


class FixtureSource:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.binding = "1" * 64
        self.reads = 0
        self.on_read = None

    def __call__(self):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return {"candidate": integration.build_candidate(self.documents), "binding": self.binding}


@pytest.fixture
def seed():
    path = Path(__file__).resolve().parents[1] / "config/preparation/ontology.seed.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def workspace(tmp_path, seed, documents):
    source = FixtureSource(documents)
    engine = integration.CrossdocOntologyWorkspace.initialize(
        tmp_path / "ontology-only", integration.integration_seed(seed), source)
    return engine, source


def approve(engine):
    baseline = engine.status()["head"]
    candidate = engine.submit()
    active = engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    return baseline, candidate, active


def query(engine, **changes):
    return engine.query(dict(SCENARIO_FACTS, **changes))


def assert_paused(result):
    assert result["formally_published"] is False
    assert result["formal_approval"] is False
    assert result["current_applicability"] == "unknown"
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert result["actual_issuance_date"] is None and result["actual_gap_days"] is None


def test_pending_schema_cannot_execute_until_explicit_review(workspace):
    engine, source = workspace
    baseline = engine.status()["head"]
    assert query(engine)["status"] == "UNSUPPORTED_RELATION"
    candidate = engine.submit()
    reads = source.reads
    pending = query(engine)
    assert pending["status"] == "UNSUPPORTED_RELATION" and pending["trace"] == []
    assert source.reads == reads and engine.status()["head"] == baseline
    assert_paused(pending)
    engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    assert query(engine)["status"] == "derived"


@pytest.mark.parametrize("actor,confirmed", [
    (ACTOR, False), (ACTOR, 1), ({"kind": "ai", "name": "Fixture agent"}, True),
])
def test_profile_review_retains_literal_human_confirmation_gate(workspace, actor, confirmed):
    engine, _ = workspace
    baseline, candidate = engine.status()["head"], engine.submit()
    with pytest.raises(ValueError, match="EXPLICIT_HUMAN"):
        engine.review(candidate, baseline, candidate, actor, confirmed=confirmed)
    assert engine.status()["head"] == baseline


def test_registered_plan_runs_three_dependencies_with_source_and_version_binding(workspace):
    engine, source = workspace
    _, candidate, active = approve(engine)
    result = engine.query(SCENARIO_FACTS, expected_version=active)
    assert result["status"] == "derived"
    assert result["source_conditions_result"] == "false"
    assert result["usual_waiting_working_days"] == 25
    assert result["old_card_interval_working_days"] == 22
    assert result["conclusion"] == "cannot_establish_full_cycle_coverage"
    first, second, third = result["trace"]
    assert [(n["id"], n["depends_on"]) for n in result["trace"]] == [
        ("c1", []), ("u1", ["c1"]), ("j1", ["c1", "u1"])]
    assert [n["output"] for n in result["trace"]] == [25, 22, "false"]
    assert first["output_type"] == second["output_type"] == "WorkingDayDuration"
    assert third["output_type"] == "ThreeValueCoverage"
    assert [len(n["evidence"]) for n in result["trace"]] == [3, 2, 5]
    assert {e["source_id"] for e in third["evidence"]} == {
        d["source_id"] for d in source.documents.values()}
    context = result["ontology_context"]
    assert (context["version"], context["candidate_id"], context["source_binding"]) == (
        active, candidate, source.binding)
    assert all(n["ontology_context_binding"] == context["binding"] for n in result["trace"])
    assert_paused(result)


def test_candidate_and_execution_derive_changed_numbers_from_source(workspace):
    engine, source = workspace
    segment = source.documents["cycle"]["segments"][1]
    segment["quote"] = segment["quote"].replace("20个工作日", "18个工作日").replace("增加5个工作日", "增加3个工作日")
    segment["text_sha256"] = hashlib.sha256(segment["quote"].encode()).hexdigest()
    _, candidate, _ = approve(engine)
    proposal = engine.store.get("candidates", candidate)["proposal"]
    assert proposal["relation_type"]["nodes"][0]["parameters"] == {"base": 18, "outside_increment": 3}
    result = query(engine)
    assert result["usual_waiting_working_days"] == 21
    assert result["old_card_interval_working_days"] == 21
    assert result["source_conditions_result"] == "unknown"
    assert result["conclusion"] == "coverage_not_disproved"
    assert query(engine, household="inside_province")["usual_waiting_working_days"] == 18


@pytest.mark.parametrize("change", ["missing", "unrecognized", "private_document_field"])
def test_incomplete_or_unrecognized_source_never_creates_candidate(workspace, change):
    engine, source = workspace
    if change == "missing":
        source.documents.pop("old_card")
    elif change == "private_document_field":
        source.documents["cycle"]["applicant"] = "private-fixture"
    else:
        segment = source.documents["old_card"]["segments"][1]
        segment["quote"] += "旧证超期也可使用。"
        segment["text_sha256"] = hashlib.sha256(segment["quote"].encode()).hexdigest()
    with pytest.raises(ValueError):
        engine.submit()
    assert engine.status()["candidates"] == []


@pytest.mark.parametrize("change", ["binding", "raw_pin", "recognized_clause"])
def test_source_change_invalidates_accepted_plan_even_if_reader_binding_is_reused(workspace, change):
    engine, source = workspace
    approve(engine)
    if change == "binding":
        source.binding = "2" * 64
    elif change == "raw_pin":
        source.documents["old_card"]["raw_sha256"] = "2" * 64
    else:
        segment = source.documents["cycle"]["segments"][1]
        segment["quote"] = segment["quote"].replace("20个工作日", "21个工作日")
        segment["text_sha256"] = hashlib.sha256(segment["quote"].encode()).hexdigest()
    with pytest.raises(ValueError, match="SOURCE_OR_REVIEW_CHANGED"):
        query(engine)


def test_archived_source_pause_blocks_review_and_active_execution(archive, seed, tmp_path):
    source = integration.ArchivedCrossdocSource(archive)
    engine = integration.CrossdocOntologyWorkspace.initialize(
        tmp_path / "reviewed-plan", integration.integration_seed(seed), source)
    baseline, candidate = engine.status()["head"], engine.submit()
    gates_path = archive / "gates.json"
    gates = json.loads(gates_path.read_bytes())
    gates["sources"]["old_card"]["paused_reason"] = "synthetic-pause"
    gates_path.write_text(json.dumps(gates), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_PAUSED"):
        engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    gates["sources"]["old_card"]["paused_reason"] = None
    gates_path.write_text(json.dumps(gates), encoding="utf-8")
    engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    gates["sources"]["old_card"]["paused_reason"] = "synthetic-pause"
    gates_path.write_text(json.dumps(gates), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_PAUSED"):
        query(engine)


@pytest.mark.parametrize("changes,expected", [
    ({"region": "北京市"}, "out_of_scope"),
    ({"matter": "ordinary-passport"}, "out_of_scope"),
    ({"household": "unknown"}, "out_of_scope"),
    ({"applicant": "private-fixture-answer"}, "unknown"),
    ({"normalized_same_start_and_unit": False}, "unknown"),
    ({"remaining_validity_working_days": True}, "unknown"),
    ({"lost_or_reported": True}, "unknown"),
    ({"exam_expedited": True}, "unknown"),
])
def test_registered_plan_does_not_bypass_fact_scope_or_exception_validation(workspace, changes, expected):
    engine, _ = workspace
    approve(engine)
    result = query(engine, **changes)
    assert result["status"] == expected and result["trace"] == []
    assert result["facts"] == {} and result["conclusion"] is None
    assert "private-fixture-answer" not in json.dumps(result)
    assert_paused(result)


def test_rollback_removes_executable_schema_and_invalidates_expected_version(workspace):
    engine, _ = workspace
    baseline, _, active = approve(engine)
    assert engine.rollback(baseline, active, ACTOR, confirmed=True) == baseline
    assert query(engine)["status"] == "UNSUPPORTED_RELATION"
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        engine.query(SCENARIO_FACTS, expected_version=active)
    assert engine.rollback(active, baseline, ACTOR, confirmed=True) == active
    assert query(engine)["status"] == "derived"


def test_review_rejects_stale_head_and_tampered_candidate(workspace):
    engine, _ = workspace
    baseline, candidate = engine.status()["head"], engine.submit()
    with pytest.raises(ValueError, match="BASE_VERSION_CHANGED"):
        engine.review(candidate, "0" * 64, candidate, ACTOR, confirmed=True)
    record = engine.store.get("candidates", candidate)
    record["proposal"]["relation_type"]["nodes"][0]["parameters"]["base"] = 1
    engine.store.put("candidates", candidate, record)
    with pytest.raises(ValueError, match="CANDIDATE_DIGEST_CHANGED"):
        engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    assert engine.status()["head"] == baseline


@pytest.mark.parametrize("mutation", ["removed", "endpoint", "dependency", "operator", "output_type", "entity"])
def test_semantically_tampered_registered_plan_cannot_execute_even_with_valid_version_hash(workspace, mutation):
    engine, _ = workspace
    _, _, active = approve(engine)
    data = deepcopy(engine.store.version(active)["data"])
    relation = next(r for r in data["seed"]["relation_types"] if r["name"] == "compares_coverage")
    if mutation == "removed":
        data["seed"]["relation_types"].remove(relation)
    elif mutation == "endpoint":
        relation["range"] = "ServiceChannelNotRegistered"
    elif mutation == "dependency":
        relation["nodes"][1]["depends_on"] = []
    elif mutation == "operator":
        relation["nodes"][0]["operator"] = "constant_answer"
    elif mutation == "output_type":
        relation["nodes"][0]["output_type"] = "ThreeValueCoverage"
    else:
        next(e for e in data["seed"]["entity_types"] if e["name"] == "IntervalComparison")["label"] = "changed"
    # A synthetically hostile writer recomputes a valid immutable-version hash.
    # The semantic source/review guard must still refuse this unreviewed schema.
    engine.store.publish("rules", data, active, ACTOR)
    with pytest.raises(ValueError, match="ONTOLOGY_ENDPOINT_TYPE_MISMATCH|EXECUTABLE_RELATION_NOT_REGISTERED"):
        query(engine)


@pytest.mark.parametrize("changed", ["source", "head"])
def test_query_rechecks_source_and_active_head_before_return(workspace, changed):
    engine, source = workspace
    baseline, _, _ = approve(engine)
    change_at = source.reads + 2

    def mutate(reads):
        if reads == change_at:
            if changed == "source":
                source.binding = "2" * 64
            else:
                engine.store.put("state", "active_rules", baseline)

    source.on_read = mutate
    with pytest.raises(ValueError, match="CHANGED_DURING"):
        query(engine)


def test_queries_never_persist_fictional_or_private_fact_values(workspace):
    engine, _ = workspace
    approve(engine)
    before = {p.relative_to(engine.root): p.read_bytes() for p in engine.root.rglob("*") if p.is_file()}
    assert query(engine, remaining_validity_working_days=1379)["status"] == "derived"
    assert query(engine, applicant="sensitive-not-persisted-fixture")["facts"] == {}
    after = {p.relative_to(engine.root): p.read_bytes() for p in engine.root.rglob("*") if p.is_file()}
    assert after == before


def test_distinct_profile_does_not_mutate_or_accept_unrelated_beijing_seed(seed, documents, tmp_path):
    original = deepcopy(seed)
    prepared = integration.integration_seed(seed)
    assert seed == original and prepared["execution_profile"] == integration.PROFILE
    engine = integration.CrossdocOntologyWorkspace.initialize(tmp_path / "wrong-profile", seed, FixtureSource(documents))
    with pytest.raises(ValueError, match="UNRELATED_ONTOLOGY_EXECUTION_PROFILE"):
        query(engine)
    assert "available_via" not in {r["name"] for r in prepared["relation_types"]}


def test_implementation_change_blocks_existing_registered_workspace(workspace, monkeypatch):
    engine, _ = workspace
    approve(engine)
    monkeypatch.setattr(integration.CrossdocOntologyWorkspace, "_implementation", staticmethod(lambda: "0" * 64))
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        query(engine)

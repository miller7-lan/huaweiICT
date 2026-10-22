"""Synthetic lifecycle evidence; these tests never attest public business approval."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from gov_mcp.knowledge_landing import URLS
from gov_mcp.ontology_evolution import OntologyWorkspace
from gov_mcp.ontology_seed import build_candidate


ACTOR = {"kind": "human", "name": "Synthetic fixture reviewer"}
FACTS = {"local_household": True, "age_years": 24, "card_years": 2,
         "photo_years": 2, "fingerprint_count": 2}
SCOPE = ("北京市", "resident-id-renewal", "damaged", "online")


def fixture_proposal():
    header = "提示：本市户籍居民符合以下条件可在网上办理居民身份证损坏换领业务："
    continuation = ("本市户籍居民，16周岁以上，5年之内办理过居民身份证，"
                    "5年之内采集过居民身份证照片，至少有一枚指纹注册成功。")
    pins = {"source_id": "BJ-ID-RENEWAL-2025", "url": URLS["BJ-ID-RENEWAL-2025"],
            "revision_id": "rev-synthetic-engine-test", "original_sha256": "a" * 64}
    clauses = [{"locator": "正文段落24", "text": header, "category": "identity"},
               {"locator": "正文段落25", "text": continuation, "category": "identity"}]
    return build_candidate({
        "mode": "public_candidate_preview", "status": "SOURCE_EXCERPTS",
        "formally_published": False, "business_conclusion_status": "PAUSED_UNREVIEWED",
        "region": SCOPE[0], "matter": SCOPE[1],
        "source_context": [{**pins, "selected_clauses": clauses}],
        "citations": [{**pins, "locator": clauses[0]["locator"], "quote": header,
                       "text_sha256": hashlib.sha256(header.encode()).hexdigest()}],
    })


class FixtureSource:
    """Mutable verified-source stand-in, with no network or real-source database."""

    def __init__(self):
        self.proposal = fixture_proposal()
        self.binding = "1" * 64
        self.error = None
        self.reads = 0
        self.on_read = None

    def __call__(self):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        if self.error:
            raise ValueError(self.error)
        return {"candidate": deepcopy(self.proposal), "binding": self.binding}


@pytest.fixture
def seed():
    path = Path(__file__).resolve().parents[1] / "config/preparation/ontology.seed.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def workspace(tmp_path, seed):
    source = FixtureSource()
    engine = OntologyWorkspace.initialize(tmp_path / "ontology-only", seed, source)
    return engine, source


def approve(engine):
    head = engine.status()["head"]
    candidate = engine.submit()
    version = engine.review(candidate, head, candidate, ACTOR, confirmed=True)
    return head, candidate, version


def query(engine, facts=None, scope=SCOPE):
    return engine.query(*scope, deepcopy(FACTS if facts is None else facts))


def assert_unpublished(result):
    assert result["formally_published"] is False
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"


def test_relation_requires_explicit_structural_review(workspace):
    engine, _ = workspace
    baseline = engine.status()["head"]
    before = query(engine)
    assert before["status"] == "UNSUPPORTED_RELATION"
    assert_unpublished(before)

    candidate = engine.submit()
    assert engine.submit() == candidate
    assert engine.status()["head"] == baseline
    assert any(row["id"] == candidate for row in engine.status()["candidates"])
    assert query(engine)["status"] == "UNSUPPORTED_RELATION"

    version = engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    assert version != baseline
    assert engine.status()["head"] == version
    result = query(engine)
    assert result["status"] == "SOURCE_CONDITIONS"
    assert result["source_conditions_result"] == "true"
    assert_unpublished(result)
    with pytest.raises(ValueError):
        engine.review(candidate, version, candidate, ACTOR, confirmed=True)


@pytest.mark.parametrize("facts,expected", [
    ({}, "unknown"),
    ({**FACTS, "age_years": None}, "unknown"),
    ({**FACTS, "age_years": 16}, "unknown"),
    ({**FACTS, "card_years": 5}, "unknown"),
    ({**FACTS, "photo_years": 5}, "unknown"),
    ({**FACTS, "local_household": False}, "false"),
    ({**FACTS, "age_years": 15}, "false"),
    ({**FACTS, "card_years": 6}, "false"),
    ({**FACTS, "photo_years": 6}, "false"),
    ({**FACTS, "fingerprint_count": 0}, "false"),
    ({"local_household": False}, "false"),
])
def test_all_conditions_use_three_values_and_unreviewed_boundaries(workspace, facts, expected):
    engine, _ = workspace
    approve(engine)
    result = query(engine, facts)
    assert result["status"] == "SOURCE_CONDITIONS"
    assert result["source_conditions_result"] == expected
    assert_unpublished(result)


@pytest.mark.parametrize("scope", [
    ("南京市", SCOPE[1], SCOPE[2], SCOPE[3]),
    (SCOPE[0], "unregistered-matter", SCOPE[2], SCOPE[3]),
    (SCOPE[0], SCOPE[1], "expired", SCOPE[3]),
    (SCOPE[0], SCOPE[1], SCOPE[2], "onsite"),
])
def test_relation_never_crosses_unconfirmed_scope(workspace, scope):
    engine, _ = workspace
    approve(engine)
    result = query(engine, scope=scope)
    assert result["status"] == "OUT_OF_SCOPE"
    assert result["source_conditions_result"] == "unknown"
    assert_unpublished(result)


@pytest.mark.parametrize("actor,ack", [
    (ACTOR, False), (ACTOR, 1), (ACTOR, "true"),
    ({"kind": "ai", "name": "Codex"}, True),
    ({"kind": "human", "name": ""}, True),
    ({"kind": "human", "name": "   "}, True),
])
def test_review_refuses_missing_human_attestation(workspace, actor, ack):
    engine, _ = workspace
    head = engine.status()["head"]
    candidate = engine.submit()
    with pytest.raises(ValueError):
        engine.review(candidate, head, candidate, actor, confirmed=ack)
    assert engine.status()["head"] == head


def test_review_refuses_wrong_candidate_digest_and_stale_head(workspace):
    engine, _ = workspace
    head = engine.status()["head"]
    candidate = engine.submit()
    with pytest.raises(ValueError):
        engine.review(candidate, head, "0" * 64, ACTOR, confirmed=True)
    with pytest.raises(ValueError):
        engine.review(candidate, "0" * 64, candidate, ACTOR, confirmed=True)
    assert engine.status()["head"] == head


@pytest.mark.parametrize("change", ["binding", "source_error"])
def test_source_change_blocks_review_and_active_query(workspace, change):
    engine, source = workspace
    head = engine.status()["head"]
    candidate = engine.submit()
    if change == "binding":
        source.binding = "2" * 64
    else:
        source.error = "SOURCE_DISABLED"
    with pytest.raises(ValueError):
        engine.review(candidate, head, candidate, ACTOR, confirmed=True)
    assert engine.status()["head"] == head

    source.binding, source.error = "1" * 64, None
    engine.review(candidate, head, candidate, ACTOR, confirmed=True)
    if change == "binding":
        source.binding = "2" * 64
    else:
        source.error = "SOURCE_PAUSED"
    with pytest.raises(ValueError):
        query(engine)


@pytest.mark.parametrize("operation", ["review", "query"])
def test_source_is_checked_again_before_operation_finishes(workspace, operation):
    engine, source = workspace
    head = engine.status()["head"]
    candidate = engine.submit()
    if operation == "query":
        engine.review(candidate, head, candidate, ACTOR, confirmed=True)
    change_at = source.reads + 2
    source.on_read = lambda reads: setattr(source, "binding", "2" * 64) if reads == change_at else None
    with pytest.raises(ValueError):
        if operation == "review":
            engine.review(candidate, head, candidate, ACTOR, confirmed=True)
        else:
            query(engine)
    if operation == "review":
        assert engine.status()["head"] == head


def test_query_rechecks_head_before_returning(workspace):
    engine, source = workspace
    baseline, _, active = approve(engine)
    change_at = source.reads + 2
    source.on_read = lambda reads: engine.store.put("state", "active_rules", baseline) if reads == change_at else None
    with pytest.raises(ValueError):
        query(engine)
    assert engine.status()["head"] != active


def test_rollback_restores_schema_but_cannot_revive_invalid_source(workspace):
    engine, source = workspace
    baseline, _, active = approve(engine)
    assert engine.rollback(baseline, active, ACTOR, confirmed=True) == baseline
    assert query(engine)["status"] == "UNSUPPORTED_RELATION"
    with pytest.raises(ValueError):
        engine.rollback(active, active, ACTOR, confirmed=True)
    source.binding = "2" * 64
    with pytest.raises(ValueError):
        engine.rollback(active, baseline, ACTOR, confirmed=True)
    assert engine.status()["head"] == baseline
    source.binding = "1" * 64
    assert engine.rollback(active, baseline, ACTOR, confirmed=True) == active
    assert query(engine)["source_conditions_result"] == "true"


@pytest.mark.parametrize("facts", [
    {**FACTS, "age_years": True}, {**FACTS, "card_years": False},
    {**FACTS, "local_household": 1}, {**FACTS, "age_years": float("nan")},
    {**FACTS, "photo_years": float("inf")}, {**FACTS, "fingerprint_count": -1},
    {**FACTS, "age_years": "24"}, {**FACTS, "private_identifier": "fixture-only"},
    {**FACTS, "age_years": 10 ** 500}, {**FACTS, "fingerprint_count": 1.5},
])
def test_invalid_or_unregistered_facts_fail_before_evaluation(workspace, facts):
    engine, _ = workspace
    approve(engine)
    with pytest.raises(ValueError):
        query(engine, facts)


def test_existing_business_directory_is_rejected_before_store_construction(tmp_path, seed, monkeypatch):
    root = tmp_path / "existing-business-runtime"
    root.mkdir()
    marker = root / "phase.json"
    marker.write_text('{"phase":"knowledge_update","schema_version":1}', encoding="utf-8")
    before = marker.read_bytes()

    def forbid_store(*args, **kwargs):
        pytest.fail("Business directory must be refused before Store opens SQLite")

    monkeypatch.setattr("gov_mcp.ontology_evolution.Store", forbid_store)
    with pytest.raises(ValueError):
        OntologyWorkspace.initialize(root, seed, FixtureSource())
    assert marker.read_bytes() == before
    assert list(root.iterdir()) == [marker]


def test_query_does_not_persist_personal_answers(workspace):
    engine, _ = workspace
    approve(engine)
    before = {path.relative_to(engine.root): path.read_bytes()
              for path in engine.root.rglob("*") if path.is_file()}
    facts = {**FACTS, "age_years": 47.125, "card_years": 1.23456789, "photo_years": 3.87654321}
    assert query(engine, facts)["source_conditions_result"] == "true"
    after = {path.relative_to(engine.root): path.read_bytes()
             for path in engine.root.rglob("*") if path.is_file()}
    assert after == before

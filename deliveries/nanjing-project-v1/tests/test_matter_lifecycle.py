"""State/authorization fixtures; none are real human business approvals."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil

import pytest

from gov_mcp.knowledge.store import KBError
from gov_mcp.matter_graph import MatterProofError, file_hash
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal, LifecycleError

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
BASELINE = json.loads((PACK / "rules-and-sources.json").read_text(encoding="utf-8"))
NOW = max(datetime.fromisoformat(s["checked_at"]).timestamp() for s in BASELINE["sources"]) + 10
MAINTAINER = Principal("test-maintainer", "maintainer", "automated_test")
CITIZEN = Principal("test-citizen", "citizen", "automated_test")
OTHER = Principal("test-other-citizen", "citizen", "automated_test")
STAFF = Principal("test-staff", "staff", "automated_test")
STAFF2 = Principal("test-other-staff", "staff", "automated_test")
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
         "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu",
         "old_card_valid": True}


@pytest.fixture
def service(tmp_path):
    tick = [NOW]
    runtime = MatterLifecycle(tmp_path / "service", clock=lambda: tick[0])
    return runtime, tick


def release(service, stage):
    result = service.prepare(PACK, ontology_stage=stage, principal=MAINTAINER)
    return service.release_preview(result["candidate_id"], service.head(),
        note="Automated fixture technical release, not human approval", principal=MAINTAINER)["version"]


def rewrite_fixture(tmp_path, change):
    path = tmp_path / "fixture-pack"
    shutil.copytree(PACK, path)
    data = deepcopy(BASELINE)
    change(data)
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    (path / "rules-and-sources.json").write_bytes(raw)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    manifest["rules_sha256"] = file_hash(raw)
    for item in manifest["files"]:
        if item["path"] == "rules-and-sources.json":
            item.update(sha256=file_hash(raw), bytes=len(raw))
    (path / "manifest.json").write_bytes(json.dumps(manifest).encode())
    return path


def test_real_snapshot_preview_evolution_pins_old_case_and_survives_restart(service):
    app, tick = service
    v1 = release(app, "seed")
    old_case = app.open_case(FACTS, principal=CITIZEN)
    v2 = release(app, "evolved")
    assert v1 != v2
    assert app.query(FACTS, principal=CITIZEN)["status"] == "DRAFT_PROOF_READY"
    old = app.read_case(old_case["case_id"], principal=CITIZEN)
    assert old["version"] == v1 and old["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    restarted = MatterLifecycle(app.root, clock=lambda: tick[0])
    assert restarted.head() == v2
    assert restarted.read_case(old_case["case_id"], principal=CITIZEN)["version"] == v1
    app.rollback(v1, v2, principal=MAINTAINER)
    assert app.head() == v1 and not app.query(FACTS, principal=CITIZEN)["proofs"]


def test_unchanged_prepare_or_repeat_release_does_not_create_version(service):
    app, _ = service
    v1 = release(app, "evolved")
    result = app.prepare(PACK, ontology_stage="evolved", principal=MAINTAINER)
    assert result["status"] == "UNCHANGED"
    repeated = app.release_preview(result["candidate_id"], v1,
        note="Repeat review note must not create another business version", principal=MAINTAINER)
    assert repeated["status"] == "UNCHANGED" and app.head() == v1
    with app.store.db() as db:
        assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 1


def test_compare_and_swap_rejects_obsolete_release_and_rollback(service):
    app, _ = service
    v1 = release(app, "seed")
    candidate = app.prepare(PACK, ontology_stage="evolved", principal=MAINTAINER)["candidate_id"]
    with pytest.raises(KBError, match="BASE_VERSION_CHANGED"):
        app.release_preview(candidate, None, note="stale caller", principal=MAINTAINER)
    v2 = app.release_preview(candidate, v1, note="current caller", principal=MAINTAINER)["version"]
    with pytest.raises(KBError, match="BASE_VERSION_CHANGED"):
        app.rollback(v1, v1, principal=MAINTAINER)
    assert app.head() == v2


def test_handoff_requires_explicit_code_and_assignee_and_never_approves_business(service):
    app, _ = service
    release(app, "evolved")
    case = app.open_case(FACTS, principal=CITIZEN)
    key = case["case_id"]
    for actor in (OTHER, STAFF, MAINTAINER):
        with pytest.raises(LifecycleError):
            app.read_case(key, principal=actor)
    handoff = app.handoff(key, principal=CITIZEN)
    with pytest.raises(LifecycleError):
        app.accept(key, "x" * 32, principal=STAFF)
    received = app.accept(key, handoff["handoff_token"], principal=STAFF)
    assert received["version"] == case["version"] and received["state"] == "staff_received"
    with pytest.raises(LifecycleError):
        app.accept(key, handoff["handoff_token"], principal=STAFF2)
    with pytest.raises(LifecycleError):
        app.read_case(key, principal=STAFF2)
    with pytest.raises(LifecycleError):
        app.confirm_handoff(key, acknowledge=False, note="not confirmed", principal=STAFF)
    confirmed = app.confirm_handoff(key, acknowledge=True, note="fixture only", principal=STAFF)
    assert confirmed["state"] == "staff_confirmed" and not confirmed["formal_business_approval"]
    assert handoff["handoff_token"].encode() not in app.store.db_path.read_bytes()


def test_pause_blocks_historical_queries_after_rollback(service):
    app, _ = service
    v1 = release(app, "seed")
    v2 = release(app, "evolved")
    app.set_source_enabled("NJ-OLD-CARD-2025", False, reason="fixture withdrawal", principal=MAINTAINER)
    for version in (v1, v2):
        with pytest.raises(LifecycleError, match="SOURCE_PAUSED"):
            app.query(FACTS, principal=CITIZEN, version_id=version)
    app.rollback(v1, v2, principal=MAINTAINER)
    with pytest.raises(LifecycleError, match="SOURCE_PAUSED"):
        app.query(FACTS, principal=CITIZEN)


def test_paused_handoff_does_not_consume_code_or_confirm(service):
    app, _ = service
    release(app, "evolved")
    case = app.open_case(FACTS, principal=CITIZEN)
    handoff = app.handoff(case["case_id"], principal=CITIZEN)
    app.set_source_enabled("NJ-OLD-CARD-2025", False, reason="fixture pause", principal=MAINTAINER)
    with pytest.raises(LifecycleError, match="SOURCE_PAUSED"):
        app.accept(case["case_id"], handoff["handoff_token"], principal=STAFF)
    app.set_source_enabled("NJ-OLD-CARD-2025", True, reason="fixture explicit restore", principal=MAINTAINER)
    assert app.accept(case["case_id"], handoff["handoff_token"], principal=STAFF)["state"] == "staff_received"


def test_staleness_cannot_be_refreshed_by_reloading_old_package(service):
    app, tick = service
    release(app, "evolved")
    tick[0] += 86401
    result = app.prepare(PACK, ontology_stage="evolved", principal=MAINTAINER)
    assert result["status"] == "UNCHANGED"
    with pytest.raises(LifecycleError, match="SOURCE_STALE"):
        app.query(FACTS, principal=CITIZEN)
    with pytest.raises(LifecycleError, match="SOURCE_STALE"):
        app.release_preview(result["candidate_id"], app.head(), note="still stale", principal=MAINTAINER)


def test_source_metadata_change_immediately_blocks_old_version(service, tmp_path):
    app, _ = service
    old = release(app, "evolved")
    # Controlled metadata mutation only, not a real official policy change.
    fixture = rewrite_fixture(tmp_path, lambda p: p["sources"][3].update(published_at="2000-01-01"))
    result = app.prepare(fixture, ontology_stage="evolved", principal=MAINTAINER)
    assert result["changed_sources"] == ["LAW-ID-BJ-TRAFFIC-2011"]
    assert app.head() == old
    with pytest.raises(LifecycleError, match="SOURCE_CHANGED"):
        app.query(FACTS, principal=CITIZEN, version_id=old)


def test_future_observation_time_is_rejected(service, tmp_path):
    app, _ = service
    fixture = rewrite_fixture(tmp_path,
        lambda p: p["sources"][0].update(checked_at="2099-01-01T00:00:00+00:00"))
    with pytest.raises(LifecycleError, match="时间"):
        app.prepare(fixture, ontology_stage="seed", principal=MAINTAINER)
    assert app.head() is None


@pytest.mark.parametrize("actor", [CITIZEN, STAFF, Principal("test-model", "model")])
def test_unprivileged_actor_cannot_prepare_or_release(service, actor):
    app, _ = service
    with pytest.raises(LifecycleError):
        app.prepare(PACK, ontology_stage="evolved", principal=actor)


def test_no_actual_business_approval_can_be_created_by_engineering_release(service):
    app, _ = service
    version = release(app, "evolved")
    assert app.store.version(version)["data"]["human_business_review"] is False
    with pytest.raises(LifecycleError, match="BUSINESS_PUBLICATION_PENDING"):
        app.publish_business(principal=Principal("fixture-professional", "professional", "automated_test"))


def test_immutable_version_and_original_corruption_are_detected(service):
    app, _ = service
    version = release(app, "evolved")
    record = app.store.version(version)["data"]["candidate"]
    original_key = record["files"]["originals/NJ-OLD-CARD-2025.html"]
    (app.store.root / "objects" / original_key).write_bytes(b"corrupted")
    with pytest.raises(KBError, match="OBJECT_INTEGRITY_FAILURE"):
        app.query(FACTS, principal=CITIZEN)


def test_existing_unrelated_runtime_is_never_initialized(tmp_path):
    (tmp_path / "existing.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(LifecycleError, match="既有业务"):
        MatterLifecycle(tmp_path)
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "preserve"

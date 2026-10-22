"""Isolated synthetic source-database checks, never a live-source acceptance claim."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge_landing import URLS
from gov_mcp.ontology_evolution import OntologyWorkspace, PublicOntologySource


def write_snapshot(store, snapshot):
    (store.root / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def public_source(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-source-only")
    raw = ('<div class="fixture"><p>'
           '提示：本市户籍居民符合以下条件可在网上办理居民身份证损坏换领业务：'
           '</p><p>本市户籍居民，16周岁以上，5年之内办理过居民身份证，'
           '5年之内采集过居民身份证照片，至少有一枚指纹注册成功。'
           '</p></div>').encode("utf-8")
    chunks, _ = parse_html(raw, {"class": "fixture"}, min_text_length=1)
    raw_hash = hashlib.sha256(raw).hexdigest()
    source_id = "BJ-ID-RENEWAL-2025"
    spec = {"source_id": source_id, "url": URLS[source_id], "region": "北京市",
            "matter": "resident-id-renewal", "selector": {"class": "fixture"},
            "original_sha256": raw_hash, "parsed_paragraph_count": 2,
            "selected_ordinals": [1, 2], "scope_limits": "Synthetic source fixture; no current applicability claim"}
    metadata = {"title": "Synthetic adapter boundary fixture", "region": spec["region"],
                "matter": spec["matter"], "document_type": "official_service_guide",
                "effective_from": None, "effective_to": None, "published_at": None,
                "usage_basis": "Self-authored fixture only; no network access", "scope_confirmed": False}
    document = {"source_id": source_id, "url": spec["url"], "metadata": metadata,
                "original_sha256": raw_hash, "selected_clauses": [
                    {"locator": chunk["locator"], "category": chunk["category"],
                     "characters": len(chunk["text"]),
                     "text_sha256": hashlib.sha256(chunk["text"].encode()).hexdigest()}
                    for chunk in chunks]}
    store.register({"id": source_id, "label": "Synthetic fixture", "enabled": False,
                    "origin": "https://www.beijing.gov.cn", "allowed_paths": ["/fuwu/"],
                    "seeds": [spec["url"]], "interval_seconds": 86400, "delay_seconds": 5,
                    "metadata": metadata})
    # Test-only direct enable does not fabricate an access or business review.
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites").fetchone()[0])
        config["enabled"] = True
        db.execute("UPDATE sites SET config=?", (json.dumps(config),))
    revision = store.ingest(source_id, spec["url"], raw, metadata, chunks)
    snapshot = {"captured_at": datetime.now(timezone.utc).isoformat(),
                "bindings": [{"spec": spec, "document": document, "revision_id": revision}],
                "acquisitions": [{"source_id": source_id, "http_status": 200,
                                  "all_selected_quotes_match": True,
                                  "status": "synthetic_fixture_not_live_acquisition"}]}
    write_snapshot(store, snapshot)
    return store, snapshot, raw


def test_adapter_opens_actual_read_only_connection_without_modifying_source(public_source):
    store, _, _ = public_source
    before = store.path.read_bytes()
    snapshot_before = (store.root / "snapshot.json").read_bytes()
    reader = PublicOntologySource(store.root)
    assert reader.store.read_only is True
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with reader.store.db() as db:
            db.execute("UPDATE sites SET failures=1")
    observed = reader()
    assert observed == reader()
    assert observed["candidate"]["status"] == "pending_review"
    assert observed["candidate"]["formally_published"] is False
    assert observed["candidate"]["formal_approval"] is False
    assert observed["freshness_basis"] == "successful_archived_snapshot_within_24h_not_live_recheck"
    assert len(observed["binding"]) == 64
    assert store.path.read_bytes() == before
    assert (store.root / "snapshot.json").read_bytes() == snapshot_before
    with store.db() as db:
        assert all(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
                   for table in ("reviews", "versions", "heads"))


def test_archive_older_than_twenty_four_hours_requires_separate_refresh(public_source):
    store, snapshot, _ = public_source
    snapshot["captured_at"] = (datetime.now(timezone.utc) - timedelta(seconds=86401)).isoformat()
    write_snapshot(store, snapshot)
    with pytest.raises(ValueError, match="ARCHIVED_SNAPSHOT_EXPIRED"):
        PublicOntologySource(store.root)()


@pytest.mark.parametrize("change", ["failures", "pause", "disabled"])
def test_current_source_failure_pause_or_disable_blocks_archive(public_source, change):
    store, _, _ = public_source
    with store.db() as db:
        if change == "failures":
            db.execute("UPDATE sites SET failures=1")
        elif change == "pause":
            db.execute("UPDATE sites SET paused_reason='Synthetic pause'")
        else:
            config = json.loads(db.execute("SELECT config FROM sites").fetchone()[0])
            config["enabled"] = False
            db.execute("UPDATE sites SET config=?", (json.dumps(config),))
    with pytest.raises(ValueError, match="SOURCE_PAUSED_OR_FAILED|SOURCE_DISABLED"):
        PublicOntologySource(store.root)()


@pytest.mark.parametrize("change", ["http_status", "quotes_match", "duplicate_receipt"])
def test_archive_receipt_must_identify_one_successful_acquisition(public_source, change):
    store, snapshot, _ = public_source
    if change == "http_status":
        snapshot["acquisitions"][0]["http_status"] = 503
    elif change == "quotes_match":
        snapshot["acquisitions"][0]["all_selected_quotes_match"] = False
    else:
        snapshot["acquisitions"].append(deepcopy(snapshot["acquisitions"][0]))
    write_snapshot(store, snapshot)
    with pytest.raises(ValueError, match="SUCCESSFUL_ARCHIVE_RECEIPT_REQUIRED"):
        PublicOntologySource(store.root)()


@pytest.mark.parametrize("change", ["original", "quote", "revision"])
def test_changed_original_stored_quote_or_revision_invalidates_saved_archive(public_source, change):
    store, snapshot, raw = public_source
    revision = snapshot["bindings"][0]["revision_id"]
    if change == "original":
        raw_hash = hashlib.sha256(raw).hexdigest()
        (store.root / "originals" / raw_hash[:2] / raw_hash).write_bytes(raw + b"changed")
    else:
        with store.db() as db:
            if change == "quote":
                db.execute("UPDATE chunks SET text='Synthetic changed quotation' WHERE ordinal=1")
            else:
                row = db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                           ("rev-new-fixture", row["document_id"], row["sha256"],
                            row["body_sha256"], "{}", row["created_at"] + 1, "pending_review"))
    with pytest.raises(ValueError):
        PublicOntologySource(store.root)()


@pytest.mark.parametrize("change", ["scope_limits", "source_config"])
def test_scope_note_or_source_config_change_invalidates_pending_candidate_binding(public_source, change):
    store, snapshot, _ = public_source
    reader = PublicOntologySource(store.root)
    before = reader()
    seed_path = Path(__file__).resolve().parents[1] / "config/preparation/ontology.seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    workspace = OntologyWorkspace.initialize(store.root.parent / "synthetic-ontology-only", seed, reader)
    head = workspace.status()["head"]
    pending_id = workspace.submit()
    if change == "scope_limits":
        snapshot["bindings"][0]["spec"]["scope_limits"] += "; amended fixture qualification"
        write_snapshot(store, snapshot)
    else:
        with store.db() as db:
            config = json.loads(db.execute("SELECT config FROM sites").fetchone()[0])
            config["delay_seconds"] = 10
            db.execute("UPDATE sites SET config=?", (json.dumps(config),))
    after = reader()
    assert after["candidate"] == before["candidate"]
    assert after["binding"] != before["binding"]
    actor = {"kind": "human", "name": "Synthetic structural reviewer fixture"}
    with pytest.raises(ValueError, match="SOURCE_CHANGED_SINCE_PROPOSAL"):
        workspace.review(pending_id, head, pending_id, actor, confirmed=True)
    assert workspace.status()["head"] == head

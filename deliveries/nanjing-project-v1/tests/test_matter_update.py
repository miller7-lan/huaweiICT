"""Update-boundary fixtures, not real HTTP observations or business acceptance."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from gov_mcp.knowledge.store import KBError
from gov_mcp.matter_graph import load_pack, file_hash
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal, LifecycleError
from gov_mcp.matter_native import FACTS
from gov_mcp.matter_update import MatterUpdater, build_snapshot, validate_snapshot, inspect_capture, encoded

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
BASE = load_pack(PACK)
IDS = [s["source_id"] for s in BASE["sources"]]
LOCAL = [i for i in IDS if not i.startswith("LAW")]
NOW = max(datetime.fromisoformat(s["checked_at"]).timestamp() for s in BASE["sources"]) + 20
MAINT = Principal("update-fixture-maint", "maintainer", "automated_test")
CITIZEN = Principal("update-fixture-citizen", "citizen", "automated_test")


def capture_fixture(path, *, change=None, checked=NOW):
    path.mkdir()
    records = []
    for source in BASE["sources"]:
        raw = (PACK / source["original_file"]).read_bytes()
        if change: raw = change(source["source_id"], raw)
        filename = source["source_id"] + ".html"
        (path / filename).write_bytes(raw)
        records.append({"source_id": source["source_id"], "url": source["url"],
            "status": "captured_pending_review", "filename": filename,
            "checked_at": datetime.fromtimestamp(checked, timezone.utc).isoformat(),
            "bytes": len(raw), "original_sha256": file_hash(raw),
            "requests": [{"url": source["url"], "http_status": 200, "bytes": len(raw), "sha256": file_hash(raw)}]})
    (path / "snapshot.json").write_bytes(encoded({"schema": 1, "test_fixture": True, "sources": records}))
    return path


@pytest.fixture
def update(tmp_path):
    tick = [NOW]
    lifecycle = MatterLifecycle(tmp_path / "service", clock=lambda: tick[0])
    updater = MatterUpdater(tmp_path / "updates", lifecycle, clock=lambda: tick[0])
    baseline = tmp_path / "baseline"
    build_snapshot(PACK, baseline, LOCAL)
    staged = updater.stage(baseline, ontology_stage="seed", expected_head=None, principal=MAINT)
    version = updater.release_engineering(staged["update_id"], note="Automated controlled baseline fixture", principal=MAINT)["version"]
    return updater, lifecycle, tick, version, baseline


def reseal(root, mutate):
    p = root / "rules-and-sources.json"
    data = json.loads(p.read_text(encoding="utf-8")); mutate(data); p.write_bytes(encoded(data))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["rules_sha256"] = file_hash(p.read_bytes())
    for row in manifest["files"]:
        raw = (root / row["path"]).read_bytes(); row.update(sha256=file_hash(raw), bytes=len(raw))
    (root / "manifest.json").write_bytes(encoded(manifest))


def test_whole_document_unchanged_refresh_keeps_semantic_version_and_replays_once(update, tmp_path):
    app, service, tick, old, _ = update
    observed = tmp_path / "observed"
    build_snapshot(PACK, observed, LOCAL, capture_root=capture_fixture(tmp_path / "capture"))
    before = len(service.store.rows("candidates"))
    staged = app.stage(observed, ontology_stage="seed", expected_head=old, principal=MAINT)
    assert staged["status"] == "NO_SEMANTIC_CHANGE" and not staged["replayed"]
    assert app.stage(observed, ontology_stage="seed", expected_head=old, principal=MAINT)["replayed"]
    assert app.release_engineering(staged["update_id"], note="Actual-source fixture observation only", principal=MAINT)["version"] == old
    assert len(service.store.rows("versions")) == 1 and len(service.store.rows("candidates")) == before
    assert all(service.store.get("sources", i)["checked_at"] == NOW for i in LOCAL)


def test_real_original_addition_and_ontology_stage_are_both_required(update, tmp_path):
    app, service, tick, old, _ = update
    old_case = service.open_case(FACTS, principal=CITIZEN)
    assert old_case["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    target = tmp_path / "four"
    build_snapshot(PACK, target, IDS, capture_root=capture_fixture(tmp_path / "capture"))
    source_only = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    assert source_only["delta"]["added_rules"] == ["NR01", "NR13", "NR14", "NR15"]
    assert [r["source_id"] for r in source_only["delta"]["sources"] if r["kind"] == "source_added"] == ["LAW-ID-BJ-TRAFFIC-2011"]
    middle = app.release_engineering(source_only["update_id"], note="Fixture source registration; no human approval", principal=MAINT)["version"]
    assert service.query(FACTS, principal=CITIZEN)["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    evolved = app.stage(target, ontology_stage="evolved", expected_head=middle, principal=MAINT)
    new = app.release_engineering(evolved["update_id"], note="Fixture typed engineering evolution", principal=MAINT)["version"]
    assert len({old, middle, new}) == 3 and service.query(FACTS, principal=CITIZEN)["status"] == "DRAFT_PROOF_READY"
    assert service.read_case(old_case["case_id"], principal=CITIZEN)["version"] == old
    assert service.read_case(old_case["case_id"], principal=CITIZEN)["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"


def test_body_change_outside_old_citations_pauses_history_before_any_release(update, tmp_path):
    app, service, tick, old, _ = update
    old_case = service.open_case(FACTS, principal=CITIZEN)
    def change(identifier, raw):
        if identifier == "NJ-POLICE-FAQ-2025":
            # The old review selected paragraphs 14..33. Change a header outside that selection.
            return raw.replace("居民身份证".encode(), "居民身份凭证".encode(), 1)
        return raw
    capture = capture_fixture(tmp_path / "capture", change=change)
    # If the first occurrence is outside the body, use an appended body paragraph instead.
    from gov_mcp.matter_update import page_data
    source = BASE["sources"][0]; before_rows, _ = page_data((PACK / source["original_file"]).read_bytes(), source["selector"])
    raw = (capture / (source["source_id"] + ".html")).read_bytes()
    after_rows, _ = page_data(raw, source["selector"])
    if before_rows == after_rows:
        original = before_rows[0]["text"].encode()
        raw = (PACK / source["original_file"]).read_bytes().replace(original, original + "（全正文变化测试）".encode(), 1)
        capture = capture_fixture(tmp_path / "capture-body", change=lambda i, r: raw if i == source["source_id"] else r)
    target = tmp_path / "changed"
    build_snapshot(PACK, target, LOCAL, capture_root=capture)
    staged = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    assert staged["status"] == "SOURCE_REVIEW_REQUIRED"
    with pytest.raises(ValueError, match="更新尚未通过"): app.release_engineering(staged["update_id"], note="blocked", principal=MAINT)
    with pytest.raises(LifecycleError, match="SOURCE_PAUSED"): service.read_case(old_case["case_id"], principal=CITIZEN)


def test_capture_receipt_cannot_be_replaced_by_timestamp_edit(update, tmp_path):
    _, service, tick, old, _ = update
    target = tmp_path / "snapshot"
    build_snapshot(PACK, target, LOCAL, capture_root=capture_fixture(tmp_path / "capture"))
    reseal(target, lambda p: p["sources"][0].update(checked_at=datetime.fromtimestamp(NOW+1, timezone.utc).isoformat()))
    with pytest.raises(ValueError, match="未绑定"): validate_snapshot(target, now=NOW+2, max_age_seconds=86400)


def test_omitted_whole_body_paragraph_is_rejected_even_with_new_manifest(update, tmp_path):
    target = tmp_path / "snapshot"
    build_snapshot(PACK, target, LOCAL, capture_root=capture_fixture(tmp_path / "capture"))
    reseal(target, lambda p: p["sources"][0]["segments"].pop())
    with pytest.raises(ValueError, match="全部正文"): validate_snapshot(target, now=NOW, max_age_seconds=86400)


def test_source_metadata_change_blocks_even_when_text_unchanged(update, tmp_path):
    app, service, tick, old, _ = update
    capture = capture_fixture(tmp_path / "capture", change=lambda i, r:
        r.replace(b"</head>", b'<meta name="effectdate" content="2099-01-01"></head>') if i == LOCAL[0] else r)
    target = tmp_path / "snapshot"; build_snapshot(PACK, target, LOCAL, capture_root=capture)
    staged = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    assert staged["status"] == "SOURCE_REVIEW_REQUIRED"
    assert any(r["kind"] == "page_metadata_changed" for r in staged["delta"]["sources"])


def test_failed_capture_blocks_only_known_affected_source_and_never_resumes(update, tmp_path):
    app, service, tick, old, _ = update
    capture = capture_fixture(tmp_path / "capture")
    result = app.block_capture(capture, [LOCAL[0]], reason="Explicit fixture transport stop", principal=MAINT)
    assert result["status"] == "SOURCE_REVIEW_REQUIRED" and not service.store.get("sources", LOCAL[0])["enabled"]
    target = tmp_path / "snapshot"; build_snapshot(PACK, target, LOCAL, capture_root=capture)
    staged = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    with pytest.raises(LifecycleError, match="SOURCE_PAUSED"):
        app.release_engineering(staged["update_id"], note="No automatic source resume", principal=MAINT)


def test_old_archive_cannot_refresh_source_or_overwrite_newer_observation(update, tmp_path):
    app, service, tick, old, baseline = update
    with pytest.raises(ValueError, match="归档基线"): app.stage(baseline, ontology_stage="seed", expected_head=old, principal=MAINT)
    capture = capture_fixture(tmp_path / "capture")
    target = tmp_path / "snapshot"; build_snapshot(PACK, target, LOCAL, capture_root=capture)
    service.store.put("sources", LOCAL[0], {**service.store.get("sources", LOCAL[0]), "checked_at": NOW+5})
    with pytest.raises(ValueError, match="较旧回执"): app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)


def test_release_cas_and_role_boundary(update, tmp_path):
    app, service, tick, old, _ = update
    target = tmp_path / "snapshot"; build_snapshot(PACK, target, IDS, capture_root=capture_fixture(tmp_path / "capture"))
    with pytest.raises(LifecycleError, match="权限"): app.stage(target, ontology_stage="seed", expected_head=old, principal=CITIZEN)
    with pytest.raises(KBError, match="BASE_VERSION_CHANGED"): app.stage(target, ontology_stage="seed", expected_head=None, principal=MAINT)
    a = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    b = app.stage(target, ontology_stage="evolved", expected_head=old, principal=MAINT)
    app.release_engineering(b["update_id"], note="Fixture competing release", principal=MAINT)
    with pytest.raises(KBError, match="BASE_VERSION_CHANGED"): app.release_engineering(a["update_id"], note="stale", principal=MAINT)


def test_source_removal_pauses_current_history_and_capture_inspection_is_honest(update, tmp_path):
    app, service, tick, old, _ = update
    capture = capture_fixture(tmp_path / "capture")
    assert all(r["kind"] == "unchanged" for r in inspect_capture(PACK, capture, IDS))
    target = tmp_path / "snapshot"; build_snapshot(PACK, target, LOCAL[1:], capture_root=capture)
    staged = app.stage(target, ontology_stage="seed", expected_head=old, principal=MAINT)
    assert staged["status"] == "SOURCE_REVIEW_REQUIRED" and LOCAL[0] in staged["blocked_sources"]
    assert not service.store.get("sources", LOCAL[0])["enabled"]


def test_new_source_changed_quote_cannot_reuse_template_or_pause_unrelated_sources(update, tmp_path):
    app, service, tick, old, _ = update
    law = "LAW-ID-BJ-TRAFFIC-2011"
    def change(identifier, raw):
        if identifier == law:
            assert "六十日".encode() in raw
            return raw.replace("六十日".encode(), "六十一日".encode(), 1)
        return raw
    capture = capture_fixture(tmp_path / "capture", change=change)
    observed = inspect_capture(PACK, capture, IDS)
    assert next(r["kind"] for r in observed if r["source_id"] == law) == "body_changed"
    app.block_capture(capture, [law], reason="Changed new-source legal quote requires fresh review", principal=MAINT)
    with pytest.raises(ValueError, match="原引用失效"):
        build_snapshot(PACK, tmp_path / "invalid", IDS, capture_root=capture)
    assert service.head() == old and all(service.store.get("sources", source)["enabled"] for source in LOCAL)

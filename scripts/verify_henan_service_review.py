"""Verify this attributed source review, draft staleness and isolated replay."""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_fetch import parse_html, select_paragraph_keywords, worker_once
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对省厅服务信息复核及隔离重放，不执行模型或发布")
    parser.add_argument("--review-packet", required=True, type=Path)
    args = parser.parse_args()
    if read(ROOT / "evaluation/knowledge/holdout-business-reference-draft.json").get("draft_revision", 2) >= 3:
        parser.error("该脚本核对D26迁移前的历史状态；请使用verify_holdout_draft.py，不覆盖原阶段回执")
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    assessment = read(ROOT / "evaluation/knowledge/henan-service-review-20261017.json")
    before = read(store.root / "source-research/henan-service-review-20261017/before.json")
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    readiness = read(ROOT / "evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    packet = read(args.review_packet)
    checks = {}
    with store.db() as db:
        row, digest = store._verify(db, assessment["revision"], require_review=True)
        _, chunks = store._content(db, row["id"])
        metadata = json.loads(row["metadata"])
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (assessment["source_id"],)).fetchone()[0])
        _, old_chunks = store._content(db, assessment["previous_revision"])
        counts = {t: db.execute("SELECT count(*) FROM " + t).fetchone()[0]
                  for t in ("reviews", "snapshot_evaluations", "versions", "heads")}
    raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
    parsed, _ = parse_html(raw, config["content_selector"], min_text_length=8,
                           excluded_selectors=config.get("excluded_content_selectors"))
    selected = select_paragraph_keywords(parsed, config["include_paragraph_keywords"], config["paragraph_expected_count"])
    checks["review_is_latest_and_digest_bound"] = latest == row["id"] and digest == assessment["review_digest"]
    checks["original_bytes_unchanged"] = fingerprint(raw) == before["revision"]["sha256"] == row["sha256"]
    checks["canonical_worker_selection_is_complete_first_measure"] = selected == chunks == old_chunks[:1] and len(chunks) == 1
    checks["unselected_background_still_in_original_and_history"] = len(old_chunks) == 2 and old_chunks[1] in parsed
    checks["source_observation_is_immutable"] = metadata["source_observation"] == json.loads(before["revision"]["metadata"])["source_observation"]
    checks["publication_date_is_explicitly_not_legal_commencement"] = (metadata["date_basis"] == assessment["date_basis"]
        and metadata["published_at"] == "2026-05-05" and metadata["effective_from"] is None
        and metadata["effective_to"] is None and metadata["date_basis"]["legal_commencement_established"] is False)
    checks["review_identity_and_scope_are_honest"] = (metadata["source_fact_assessment"] == assessment["assessment"]
        and assessment["reviewer_kind"] == "ai" and assessment["human_review_completed"] is False
        and metadata["scope_confirmed"] is True)
    checks["one_new_source_review_without_publication"] = counts == dict(before["gates"], reviews=before["gates"]["reviews"] + 1)
    checks["publication_policy_preserved"] = fingerprint((store.root / "publication-policy.json").read_bytes()) == before["policy_sha256"]
    catalog = read(ROOT / "data/knowledge/candidate-catalog.json")
    exported = next(d for d in catalog["documents"] if d["source_id"] == assessment["source_id"])
    checks["catalog_exports_exact_date_basis_and_scoped_selection"] = (exported["revision"] == row["id"]
        and exported["metadata"]["date_basis"] == metadata["date_basis"] and len(exported["selected_clauses"]) == 1)
    checks["readiness_reports_actual_service_review"] = (readiness["recorded_reviewed_source_count"] == 2
        and len(readiness["reviewed_service_information"]) == 1
        and readiness["reviewed_service_information"][0]["revision"] == row["id"])
    holdout = packet["holdout_design_draft"]
    checks["stale_D26_binding_is_detected_without_approval"] = (holdout["stale_source_keys"] == ["D26"]
        and holdout["live_binding_checks"]["sources_match_latest"] is False
        and holdout["live_binding_checks"]["development_registry_matches"] is True
        and holdout["automatic_approval"] is False)
    draft_checks = read(ROOT / "evidence/knowledge-update/holdout-draft-integrity.json")
    failed = sorted(k for k, v in draft_checks["checks"].items() if not v)
    checks["draft_verifier_truthfully_withholds_integrity"] = (draft_checks["draft_integrity_passed"] is False and failed == sorted([
        "latest_originals_and_literal_anchor_spans_match", "actual_export_contains_both_drafts_and_preserves_empty_approvals"]))
    manifest = read(ROOT / "evaluation/knowledge/archive/holdout-v2-before-service-review-20261017/manifest.json")
    checks["v2_archive_and_active_semantics_preserved"] = (fingerprint({k:v for k,v in manifest.items() if k != "content_sha256"}) == manifest["content_sha256"]
        and all(fingerprint((ROOT / item["path"]).read_bytes()) == item["sha256"]
            and read(ROOT / item["path"]) == read(ROOT / item["source_path"]) for item in manifest["files"]))
    parent = ROOT / ".runtime/knowledge-update-restores"
    ensure_private_directory(parent)
    restored = restore_bundle(backup["backup"]["directory"], parent / ("henan-service-" + uuid.uuid4().hex[:10]))
    clone = KnowledgeStore(restored["restored_to"])
    with clone.db() as db:
        copied, copied_digest = clone._verify(db, row["id"], require_review=True)
        copied_metadata = json.loads(copied["metadata"])
        queued = db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0]
    checks["full_restore_preserves_service_review_and_date_basis"] = (copied_digest == digest
        and copied_metadata == metadata and restored["services_started"] is False and queued == 0)
    try:
        clone.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        candidate_error = str(error)
    else:
        candidate_error = None
    checks["other_pending_sources_still_prevent_candidate"] = candidate_error == "Unreviewed or changed content"

    class ArchivedFixture:
        def __init__(self, payload):
            self.payload = payload
            self.calls = 0

        def fetch(self, actual_config, url):
            assert actual_config["id"] == assessment["source_id"] and url == assessment["url"]
            self.calls += 1
            return 200, {"content-type": "text/html"}, self.payload, url

    def replay(payload):
        with clone.db() as db:
            db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=?", (assessment["source_id"],))
        fixture = ArchivedFixture(payload)
        assert worker_once(clone, fetcher=fixture, owner="isolated-service-review-replay") is True
        with clone.db() as db:
            job = db.execute("SELECT state,error FROM jobs WHERE site_id=?", (assessment["source_id"],)).fetchone()
            actual = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1", (assessment["source_id"],)).fetchone()
        return fixture.calls == 1 and job["state"] == "done" and job["error"] is None, actual

    ok, same = replay(raw)
    checks["actual_config_unchanged_replay_preserves_review"] = ok and same["id"] == row["id"] and same["state"] == "reviewed"
    changed_raw = raw.replace("15个工作日".encode(), "14个工作日".encode())
    assert changed_raw != raw
    ok, changed = replay(changed_raw)
    changed_metadata = json.loads(changed["metadata"])
    checks["synthetic_body_change_requires_new_review"] = (ok and changed["id"] != row["id"] and changed["state"] == "pending_review"
        and changed_metadata.get("date_basis") is None and changed_metadata["effective_from"] is None
        and changed_metadata["published_at"] is None)
    with store.db() as db:
        final, final_digest = store._verify(db, row["id"], require_review=True)
        final_counts = {t:db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in counts}
    checks["isolation_did_not_change_live_review_or_publication"] = final_digest == digest and final_counts == counts
    report = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
        "checks": checks, "revision": row["id"], "counts": store.status()["counts"], "formal_gates": counts,
        "draft_stale_sources": holdout["stale_source_keys"], "expected_draft_failed_checks": failed,
        "isolated_replay_uses_archived_then_synthetically_mutated_bytes": True,
        "fixture_is_not_a_new_policy": True, "network_requests_by_replay": 0, "new_model_runs": 0,
        "candidate_error": candidate_error, "business_acceptance_completed": False,
        "windows_task_touched": False}
    (ROOT / "evidence/knowledge-update/henan-service-review-integrity.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":checks,"new_model_runs":0},ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

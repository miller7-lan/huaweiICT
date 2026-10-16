"""Verify a dated provincial service fact without inventing legal commencement."""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_dates import reviewed_start
from gov_mcp.knowledge_fetch import parse_html, select_paragraph_keywords, worker_once
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对六个月服务报道复核、日期及隔离重放，不执行模型或发布")
    parser.add_argument("--review-packet", type=Path, required=True)
    args = parser.parse_args()
    source = read(ROOT / "evaluation/knowledge/henan-renewal-window-service-review-20261017.json")
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    before = read(store.root / "source-research/henan-window-review-20261017/before.json")
    readiness = read(ROOT / "evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    packet = read(args.review_packet)
    holdout = packet["holdout_design_draft"]
    with store.db() as db:
        row,digest = store._verify(db,source["revision"],require_review=True)
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1",(row["document_id"],)).fetchone()[0]
        _,chunks = store._content(db,row["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(source["source_id"],)).fetchone()[0])
        gates = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]}
        if latest != row["id"] or gates != dict(before["gates"],reviews=before["gates"]["reviews"]+1):
            parser.error("此来源/闸门已有后续变化，不覆盖本阶段回执")
        metadata = json.loads(row["metadata"])
        start = reviewed_start(store,db,row,metadata)
        watch = db.execute("SELECT r.state FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id='MPS-NORM-WINDOW-WATCH' ORDER BY r.rowid DESC LIMIT 1").fetchone()[0]
    original = (store.root/"originals"/row["sha256"][:2]/row["sha256"]).read_bytes()
    parsed,_ = parse_html(original,config["content_selector"],excluded_selectors=config.get("excluded_content_selectors"))
    selected = select_paragraph_keywords(parsed,config["include_paragraph_keywords"],config["paragraph_expected_count"])
    checks = {
        "review_is_latest_and_digest_bound":digest == source["review_digest"],
        "unchanged_original_and_complete_literal_paragraph":fingerprint(original) == before["revision"]["sha256"] == source["original_sha256"] and chunks == selected == before["chunks"] and len(chunks) == 1,
        "original_observation_unchanged":metadata["source_observation"] == json.loads(before["revision"]["metadata"])["source_observation"],
        "publication_date_is_not_commencement_or_modification":start == "2025-07-10" and metadata["published_at"] == start and metadata["effective_from"] is None and metadata["effective_to"] is None and metadata["date_basis"] == source["date_basis"] and metadata["date_basis"]["legal_commencement_established"] is False,
        "ai_review_scope_and_unresolved_limits_bound":metadata["source_fact_assessment"] == source["assessment"] and source["reviewer_kind"] == "ai" and source["human_review_completed"] is False and metadata["scope_confirmed"] is True,
        "original_configuration_and_national_window_watch_preserved":config == before["config"] and config["enabled"] is True and watch == "pending_review",
        "draft_cases_and_screening_are_unchanged":all(fingerprint((ROOT/p).read_bytes()) == h for p,h in before["untouched_files"].items()),
        "formal_policy_and_publication_gates_preserved":fingerprint((store.root/"publication-policy.json").read_bytes()) == before["policy_sha256"] and gates == dict(before["gates"],reviews=before["gates"]["reviews"]+1),
        "current_draft_bindings_remain_honestly_stale":set(holdout["stale_source_keys"]) == set(source["expected_stale_holdout_source_keys"]) and holdout["live_binding_checks"]["sources_match_latest"] is False and holdout["automatic_approval"] is False,
    }
    catalog = read(ROOT/"data/knowledge/candidate-catalog.json")
    exported = next(d for d in catalog["documents"] if d["source_id"] == source["source_id"])
    checks["actual_catalog_and_readiness_include_dated_review"] = exported["revision"] == row["id"] and exported["metadata"]["date_basis"] == metadata["date_basis"] and readiness["recorded_reviewed_source_count"] == 5 and any(r["revision"] == row["id"] for r in readiness["reviewed_service_information"])
    backup = read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored = restore_bundle(backup["backup"]["directory"],ROOT/".runtime/knowledge-update-restores"/("window-service-"+uuid.uuid4().hex[:10]))
    clone = KnowledgeStore(restored["restored_to"])
    with clone.db() as db:
        copy,copy_digest = clone._verify(db,row["id"],require_review=True)
    checks["full_restore_preserves_review_and_exact_date_binding"] = copy_digest == digest and copy["metadata"] == row["metadata"] and restored["services_started"] is False
    try:
        clone.candidate("郑州市二七区","demo-id-renewal")
    except ValueError as error:
        candidate_error = str(error)
    else:
        candidate_error = None
    checks["other_unreviewed_sources_still_block_candidate"] = candidate_error == "Unreviewed or changed content"
    class Fixture:
        def __init__(self,payload):self.payload = payload
        def fetch(self,c,url):
            assert c["id"] == source["source_id"] and url == source["url"]
            return 200,{"content-type":"text/html"},self.payload,url
    def replay(payload):
        with clone.db() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 0
            result = db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=?",(source["source_id"],))
            assert result.rowcount == 1
        assert worker_once(clone,fetcher=Fixture(payload),owner="isolated-window-service-replay") is True
        with clone.db() as db:
            return db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1",(source["source_id"],)).fetchone()
    same = replay(original)
    checks["same_original_preserves_review"] = same["id"] == row["id"] and same["state"] == "reviewed"
    observation_change = original.replace(b'2026-07-29 16:08',b'2026-07-29 16:09')
    assert observation_change != original
    changed = replay(observation_change)
    changed_meta = json.loads(changed["metadata"])
    checks["observation_change_withholds_old_review_and_dates"] = changed["state"] == "pending_review" and changed["body_sha256"] == row["body_sha256"] and changed_meta.get("date_basis") is None and changed_meta["effective_from"] is None and changed_meta["published_at"] is None
    body_change = observation_change.replace("从3个月放宽到6个月".encode(),"从3个月放宽到7个月".encode())
    assert body_change != observation_change
    changed_body = replay(body_change)
    body_meta = json.loads(changed_body["metadata"])
    checks["synthetic_body_change_remains_unapproved"] = changed_body["state"] == "pending_review" and changed_body["body_sha256"] != row["body_sha256"] and body_meta.get("date_basis") is None and body_meta["effective_from"] is None
    with store.db() as db:
        final,final_digest = store._verify(db,row["id"],require_review=True)
        after_gates = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in gates}
        latest_states = dict(db.execute("SELECT state,count(*) FROM revisions WHERE rowid IN (SELECT max(rowid) FROM revisions GROUP BY document_id) GROUP BY state").fetchall())
    checks["main_review_and_gates_unchanged_by_replay"] = final_digest == digest and after_gates == gates
    result = {"schema":1,"captured_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
        "revision":row["id"],"counts":store.status()["counts"],"formal_gates":gates,"latest_states":latest_states,
        "draft_stale_sources":holdout["stale_source_keys"],"candidate_error":candidate_error,"new_model_runs":0,
        "replay_network_requests":0,"synthetic_changes_are_not_policies":True,"business_acceptance_completed":False,"windows_task_touched":False}
    (ROOT/"evidence/knowledge-update/renewal-window-review-integrity.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"passed":result["passed"],"checks":checks},ensure_ascii=False))
    if not result["passed"]:raise SystemExit(1)


if __name__ == "__main__":
    main()

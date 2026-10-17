"""Verify selected announcement clauses, explicit registry dates and isolated update stops."""
import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_fetch import observe_html, parse_html, select_paragraph_groups, worker_once
from gov_mcp.knowledge_pdf import extract_document
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对公告明确实施字段与所选核验/隐私条款；不发布或运行模型")
    parser.add_argument("--review-packet", type=Path, required=True)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    report = read(ROOT/"evaluation/knowledge/identity-privacy-selected-review-20261017.json")
    before = read(store.root/"source-research/use-privacy-review-20261017/before.json")
    if any(fingerprint((ROOT/path).read_bytes()) != digest for path, digest in before["protected_files"].items()):
        parser.error("草案已后续迁移，不覆盖本阶段回执")
    readiness = read(ROOT/"evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    with store.db() as db:
        row, digest = store._verify(db, report["revision"], require_review=True)
        _, chunks = store._content(db, row["id"])
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
        gates = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]}
        if latest != row["id"] or gates != dict(before["gates"], reviews=9):
            parser.error("来源/闸门后续变化，不覆盖本阶段回执")
        site = db.execute("SELECT * FROM sites WHERE id=?", (report["source_id"],)).fetchone()
        config = json.loads(site["config"])
        job = dict(db.execute("SELECT * FROM jobs WHERE site_id=?", (report["source_id"],)).fetchone())
        old, old_chunks = store._content(db, report["previous_revision"])
        waiver, _ = store._verify(db, before["waiver_row"]["id"], require_review=False)
        waiver_config = json.loads(db.execute("SELECT config FROM sites WHERE id='FEE-WAIVER-2004-ZJ-LEAD'").fetchone()[0])
        cq_registered = db.execute("SELECT 1 FROM sites WHERE id='HARDSHIP-CQ-2004-PROBE'").fetchone() is not None
    if job["state"] in ("queued", "running"):
        parser.error("正常后台作业2尚未完成；观察同一作业，不重复请求")
    meta = json.loads(row["metadata"])
    assessment = report["assessment"]
    raw = (store.root/"originals"/row["sha256"][:2]/row["sha256"]).read_bytes()
    parsed, _ = parse_html(raw, config["content_selector"])
    observed = observe_html(raw, config.get("metadata_selectors"), content_selector=config["content_selector"])
    field = observed["fields"]["block:document_fields"][0]
    date = assessment["date_findings"]
    checks = {
        "attributed_scoped_review_digest_matches": digest == report["review_digest"] and meta["source_fact_assessment"] == assessment and assessment["human_review_completed"] is False and assessment["business_approval"] is True,
        "same_original_and_exact_five_complete_paragraphs": row["sha256"] == old["sha256"] == before["base_row"]["sha256"] and chunks == [old_chunks[i-1] for i in [9,10,11,12,15]] and chunks == select_paragraph_groups(parsed, config["include_paragraph_keyword_groups"]),
        "implementation_date_comes_from_explicit_original_registry_field": fingerprint(field.encode()) == date["field_text_sha256"] and field[date["start"]:date["end"]] == meta["effective_from"] == "2016-07-15" and re.search(r"\[实施日期\]\s*2016-07-15",field) is not None,
        "first_publication_unknown_and_service_date_not_substituted": meta["published_at"] is None and meta["document_type"] == "official_announcement" and "date_basis" not in meta and date["first_publication_established"] is False and date["official_page_publication_field"] == "2016-08-11" and observed == meta["source_observation"] and date["raw_combined_meta_pubdate_preserved"] == observed["fields"]["meta:pubdate"],
        "validity_is_an_explicit_observed_signal_not_a_permanent_guarantee": re.search(r"\[有效性\]\s*是",field) is not None and assessment["source_status"]["value"] == "是" and meta["effective_to"] is None,
        "unauthorized_processing_qualifier_preserved_and_credit_clause_omitted": "不得擅自记录" in chunks[1]["text"] and "不得擅自复印、扫描" in chunks[2]["text"] and "不得扣留或者抵押" in chunks[2]["text"] and "黑名单" not in "".join(c["text"] for c in chunks),
        "normal_background_recheck_once_preserves_scoped_review": job["state"] == "done" and job["attempts"] == 1 and job["error"] is None and config["enabled"] is True and site["paused_reason"] is None and site["last_success"] >= datetime.fromisoformat(before["captured_at"]).timestamp(),
        "draft_inputs_and_publication_policy_preserved": all(fingerprint((ROOT/path).read_bytes()) == sha for path, sha in before["protected_files"].items()) and fingerprint((store.root/"publication-policy.json").read_bytes()) == before["policy_sha256"] and gates == dict(before["gates"], reviews=9),
        "hardship_pdf_and_eligibility_still_pending_without_parser_relaxation": waiver == before["waiver_row"] and waiver["state"] == "pending_review" and waiver_config["enabled"] is False,
    }
    hardship_raw = store.root/"source-research/fees-20261017/waiver-2004.pdf"
    try:
        extract_document(hardship_raw)
    except ValueError:
        blank_rejected = True
    else:
        blank_rejected = False
    probe = read(store.root/"source-research/hardship-crosscopy-20261017/receipt.json")
    checks["hardship_default_parser_and_robots_stop_preserved"] = blank_rejected and not cq_registered and probe["normal_fetcher"] is True and probe["phase"] == "robots" and probe["http_status"] == 403 and not (store.root/"source-research/hardship-crosscopy-20261017/original.html").exists()
    prior = report["previous_draft_integrity_receipt"]
    old_receipt = read(ROOT/prior["path"])
    checks["last_v4_success_receipt_archived_exactly"] = fingerprint((ROOT/prior["path"]).read_bytes()) == prior["sha256"] and len(old_receipt["checks"]) == 25 and all(old_receipt["checks"].values())
    draft = read(ROOT/"evidence/knowledge-update/holdout-draft-integrity.json")
    checks["source_review_invalidates_only_use_binding_without_answer_tuning"] = readiness["holdout_design_draft"]["stale_source_keys"] == ["USE"] and sorted(k for k,v in draft["checks"].items() if not v) == sorted(["latest_originals_and_literal_anchor_spans_match", "actual_export_contains_both_drafts_and_preserves_empty_approvals"]) and draft["reference_frozen"] is False
    checks["actual_export_reports_seventh_review_and_scoped_announcement"] = readiness["recorded_reviewed_source_count"] == 7 and len(readiness["reviewed_official_announcements"]) == 1 and readiness["reviewed_official_announcements"][0]["revision"] == row["id"]
    backup = read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored = restore_bundle(backup["backup"]["directory"], ROOT/".runtime/knowledge-update-restores"/("privacy-review-"+uuid.uuid4().hex[:10]))
    clone = KnowledgeStore(restored["restored_to"])
    with clone.db() as db:
        copied, copied_digest = clone._verify(db,row["id"],require_review=True)
        old_copy, _ = clone._verify(db,old["id"],require_review=False)
    checks["full_restore_retains_review_and_complete_old_sixteen_paragraphs"] = copied_digest == digest and copied["metadata"] == row["metadata"] and old_copy["body_sha256"] == old["body_sha256"] and old_copy["state"] == old["state"] == "pending_review" and len(old_chunks) == 16 and restored["services_started"] is False
    try:
        clone.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        candidate_error = str(error)
    else:
        candidate_error = None
    checks["unresolved_material_sources_still_block_formal_candidate"] = candidate_error == "Unreviewed or changed content"
    class Fixture:
        def __init__(self, payload):
            self.payload, self.calls = payload, 0
        def fetch(self, actual, url):
            assert actual["id"] == report["source_id"] and url == report["url"]
            self.calls += 1
            return 200, {"content-type":"text/html"}, self.payload, url
    def replay(payload):
        with clone.db() as db:
            assert db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=? AND url=?",(report["source_id"],report["url"])).rowcount == 1
        fixture = Fixture(payload)
        assert worker_once(clone, fetcher=fixture, owner="isolated-privacy-review-replay") is True
        with clone.db() as db:
            latest = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1",(report["source_id"],)).fetchone()
        assert fixture.calls == 1
        return latest
    same = replay(raw)
    checks["same_source_replay_preserves_review"] = same["id"] == row["id"] and same["state"] == "reviewed"
    changed_raw = raw.replace("公民应当自觉配合".encode(), "公民可以配合".encode())
    assert changed_raw != raw
    changed = replay(changed_raw)
    changed_meta = json.loads(changed["metadata"])
    checks["synthetic_rule_change_requires_new_review_and_dates"] = changed["state"] == "pending_review" and changed["body_sha256"] != row["body_sha256"] and changed_meta["effective_from"] is None and changed_meta["published_at"] is None and "source_fact_assessment" not in changed_meta
    status_raw = raw.replace("是".encode(), "否".encode())
    status = replay(status_raw)
    status_meta = json.loads(status["metadata"])
    checks["synthetic_validity_change_blocks_old_review_even_when_selected_body_matches"] = status["state"] == "pending_review" and status["body_sha256"] == row["body_sha256"] and "[有效性] 否" in status_meta["source_observation"]["fields"]["block:document_fields"][0] and status_meta["effective_from"] is None
    reverted = replay(raw)
    checks["source_reversion_does_not_resurrect_historical_review"] = reverted["id"] != row["id"] and reverted["state"] == "pending_review"
    result = {"schema":1,"captured_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
        "counts":store.status()["counts"],"formal_gates":gates,"revision":row["id"],"candidate_error":candidate_error,
        "draft_stale_sources":["USE"],"replay_network_requests":0,"synthetic_edits_are_not_policies":True,
        "new_model_runs":0,"business_acceptance_completed":False,"windows_task_changed":False}
    (ROOT/"evidence/knowledge-update/identity-privacy-source-integrity.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"passed":result["passed"],"checks":checks},ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

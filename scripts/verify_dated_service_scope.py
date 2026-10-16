"""Verify dated scope decisions and replay the corrected watch in an isolated restore."""
import argparse
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_fetch import observe_html, parse_html, select_paragraph_keywords, worker_once
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对历史范围排除和材料待核边界；隔离重放，无模型或发布")
    parser.add_argument("--review-packet", type=Path, required=True)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    source = read(ROOT/"evaluation/knowledge/dated-service-scope-decisions-20261017.json")
    before = read(store.root/"source-research/branch-scope-20261017/before.json")
    readiness = read(ROOT/"evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    body = {k: v for k, v in source.items() if k != "content_sha256"}
    checks = {"decision_file_has_bound_content": fingerprint(body) == source["content_sha256"]}
    bindings = source["exclusions"]+[source["reply_pending"]]
    rows, chunks, configs, originals = {}, {}, {}, {}
    with store.db() as db:
        gates = {t: db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]}
        for item in bindings:
            key = item["source_id"]
            rows[key], _ = store._verify(db, item["revision"], require_review=False)
            _, chunks[key] = store._content(db, item["revision"])
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (rows[key]["document_id"],)).fetchone()[0]
            if latest != item["revision"] or gates != before["gates"]:
                parser.error("后续来源/闸门已有变化，不覆盖本阶段回执")
            configs[key] = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (key,)).fetchone()[0])
            originals[key] = (store.root/"originals"/rows[key]["sha256"][:2]/rows[key]["sha256"]).read_bytes()
        for item in source["exclusions"]:
            store._verify_exclusion(db, item["revision"])
        transition = source["exclusions"][1]["assessment"]["newer_direct_transition"]
        newer, digest = store._verify(db, transition["revision"], require_review=True)
        _, newer_chunks = store._content(db, newer["id"])
        checks["newer_duration_relation_remains_direct_and_reviewed"] = digest == transition["review_digest"] and "由25个自然日缩短至15个工作日" in newer_chunks[0]["text"] and json.loads(newer["metadata"])["effective_from"] is None
        retained = []
        for revision, expected in before["retained_pending_digests"].items():
            row, actual = store._verify(db, revision, require_review=False)
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
            retained.append(row["state"] == "pending_review" and actual == expected and latest == revision)
        checks["mixed_2024_and_2025_pages_not_blanket_excluded_or_approved"] = all(retained)
        checks["paused_sources_and_shared_discovery_configuration_preserved"] = all(dict(db.execute("SELECT * FROM sites WHERE id=?", (old["id"],)).fetchone())["config"] == old["config"] and db.execute("SELECT paused_reason FROM sites WHERE id=?", (old["id"],)).fetchone()[0] == old["paused_reason"] for old in before["stopped_sources"]) and configs["HN-INTERPRETATION-DISCOVERY"] == before["configs"]["HN-INTERPRETATION-DISCOVERY"]
        watch_site = db.execute("SELECT * FROM sites WHERE id='HN-MEASURES-2024-JY'").fetchone()
        job = dict(db.execute("SELECT * FROM jobs WHERE site_id='HN-MEASURES-2024-JY'").fetchone())
    if job["state"] in ("queued", "running"):
        parser.error("正常后台作业23尚未终结；继续观察同一作业，不重复请求")
    checks["originals_and_all_selected_texts_preserved"] = all(rows[item["source_id"]]["sha256"] == before["rows"][item.get("key", "2023_reply")]["sha256"] and chunks[item["source_id"]] == before["chunks"][item.get("key", "2023_reply")] for item in bindings)
    checks["scope_decisions_attributed_ai_not_repeal_or_business_approval"] = all(json.loads(rows[item["source_id"]]["metadata"])["source_fact_assessment"] == item["assessment"] and item["assessment"]["business_approval"] is False and item["assessment"]["human_review_completed"] is False and item["assessment"]["not_a_repeal_or_legal_end_date"] is True and json.loads(rows[item["source_id"]]["metadata"])["effective_to"] is None for item in source["exclusions"])
    reply = source["reply_pending"]
    meta = json.loads(rows[reply["source_id"]]["metadata"])
    span = reply["assessment"]["ambiguous_parenthetical"]
    text = next(c["text"] for c in chunks[reply["source_id"]] if c["locator"] == span["locator"])
    checks["reply_parenthetical_and_branch_remain_unapproved_without_rewriting"] = rows[reply["source_id"]]["state"] == "pending_review" and meta["scope_confirmed"] is False and meta["effective_from"] is None and meta["source_fact_assessment"] == reply["assessment"] and fingerprint(text.encode()) == span["parent_text_sha256"] and text[span["start"]:span["end"]] == span["literal"] == "（需挂失申报）"
    checks["reply_publication_comes_from_original_meta_not_url_or_commencement"] = meta["published_at"] == "2023-02-06" and meta["source_observation"]["fields"]["meta:pubdate"] == ["2023-02-06 15:30"] and observe_html(originals[reply["source_id"]], content_selector=configs[reply["source_id"]]["content_selector"]) == meta["source_observation"] and "date_basis" not in meta
    refresh = source["observation_reprocessing"]
    with store.db() as db:
        initial, _ = store._verify(db, reply["initial_annotation_revision"], require_review=False)
    old_observation = json.loads(initial["metadata"])["source_observation"]
    observed_without_added_field = copy.deepcopy(meta["source_observation"])
    observed_without_added_field["fields"].pop("meta:lastmodifiedtime")
    checks["same_original_observation_reprocessing_retains_old_revision_and_date_boundary"] = observed_without_added_field == old_observation and fingerprint(old_observation) == refresh["previous_observation_sha256"] and fingerprint(meta["source_observation"]) == refresh["observation_sha256"] and refresh["original_sha256"] == rows[reply["source_id"]]["sha256"] == initial["sha256"] and refresh["network_requests"] == 0 and meta["source_observation"]["fields"]["meta:lastmodifiedtime"] == ["2024-02-20 15:05"] and initial["state"] == "pending_review"
    watch = configs["HN-MEASURES-2024-JY"]
    old = copy.deepcopy(before["configs"]["HN-MEASURES-2024-JY"])
    old["include_paragraph_keywords"] = ["一、压缩居民身份证办理时限"]
    parsed, _ = parse_html(originals[watch["id"]], watch["content_selector"])
    checks["watch_only_removed_old_numeric_value_and_still_selects_complete_clause"] = watch == old and select_paragraph_keywords(parsed, watch["include_paragraph_keywords"], 1) == chunks[watch["id"]]
    checks["normal_background_recheck_finished_once_without_reopening_unchanged_exclusion"] = job["state"] == "done" and job["attempts"] == 1 and job["error"] is None and watch_site["paused_reason"] is None and watch_site["last_success"] >= datetime.fromisoformat(before["captured_at"]).timestamp()
    checks["policy_and_frozen_draft_inputs_preserved"] = fingerprint((store.root/"publication-policy.json").read_bytes()) == before["publication_policy_sha256"] and all(fingerprint((ROOT/p).read_bytes()) == h for p, h in before["protected_files"].items()) and gates == before["gates"]
    backup = read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored = restore_bundle(backup["backup"]["directory"], ROOT/".runtime/knowledge-update-restores"/("dated-scope-"+uuid.uuid4().hex[:10]))
    clone = KnowledgeStore(restored["restored_to"])
    with clone.db() as db:
        restored_ok = []
        for item in bindings:
            row, _ = clone._verify(db, item["revision"], require_review=False)
            restored_ok.append(row["metadata"] == rows[item["source_id"]]["metadata"] and row["state"] == rows[item["source_id"]]["state"])
        for item in source["exclusions"]:
            clone._verify_exclusion(db, item["revision"])
    checks["complete_restore_preserves_scope_decisions_and_pending_note"] = all(restored_ok) and restored["services_started"] is False
    try:
        clone.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        restored_candidate_error = str(error)
    else:
        restored_candidate_error = None
    checks["actual_restored_pending_materials_still_block_candidate"] = restored_candidate_error == "Unreviewed or changed content"
    class Fixture:
        def __init__(self, sid, url, payload):
            self.sid, self.url, self.payload, self.calls = sid, url, payload, 0
        def fetch(self, config, url):
            assert config["id"] == self.sid and url == self.url
            self.calls += 1
            return 200, {"content-type": "text/html"}, self.payload, url
    def replay(item, payload):
        with clone.db() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE state='queued' AND due<=0").fetchone()[0] == 0
            assert db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=? AND url=?", (item["source_id"], item["url"])).rowcount == 1
        fixture = Fixture(item["source_id"], item["url"], payload)
        assert worker_once(clone, fetcher=fixture, owner="isolated-dated-scope-replay") is True
        with clone.db() as db:
            job = db.execute("SELECT state,error FROM jobs WHERE site_id=? AND url=?", (item["source_id"], item["url"])).fetchone()
            row = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (item["source_id"], item["url"])).fetchone()
        assert fixture.calls == 1 and job["state"] == "done" and job["error"] is None
        return row
    same = [replay(item, originals[item["source_id"]]) for item in bindings]
    checks["actual_worker_unchanged_replays_preserve_both_exclusions_and_pending_note"] = all(row["id"] == item["revision"] and row["state"] == rows[item["source_id"]]["state"] for item, row in zip(bindings, same))
    duration = source["exclusions"][1]
    changed_payload = originals[duration["source_id"]].replace("25个自然日".encode(), "24个自然日".encode())
    assert changed_payload != originals[duration["source_id"]]
    changed = replay(duration, changed_payload)
    changed_meta = json.loads(changed["metadata"])
    with clone.db() as db:
        _, changed_chunks = clone._content(db, changed["id"])
    checks["synthetic_number_change_is_captured_as_pending_without_old_exclusion_or_dates"] = changed["id"] != duration["revision"] and changed["state"] == "pending_review" and len(changed_chunks) == 1 and "24个自然日" in changed_chunks[0]["text"] and changed_meta["effective_from"] is None and changed_meta["published_at"] is None and "_excluded_after_revision" not in changed_meta and "source_fact_assessment" not in changed_meta
    try:
        clone.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        candidate_error = str(error)
    else:
        candidate_error = None
    checks["remaining_pending_sources_still_block_formal_candidate"] = candidate_error == "Unreviewed or changed content"
    exported = {doc["revision"]: doc for doc in read(ROOT/"data/knowledge/candidate-catalog.json")["documents"]}
    checks["exports_match_latest_dispositions_and_empty_approvals"] = all(exported[item["revision"]]["state"] == rows[item["source_id"]]["state"] for item in bindings) and readiness["recorded_reviewed_source_count"] == 6 and readiness["business_review_completed"] is False and readiness["holdout_design_draft"]["reference_frozen"] is False
    report = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "counts": store.status()["counts"], "formal_gates": gates, "normal_background_job": {k: job[k] for k in ["id", "state", "attempts", "error"]},
        "restored_candidate_error": restored_candidate_error, "candidate_error": candidate_error, "replay_network_requests": 0, "synthetic_change_is_not_a_policy": True,
        "new_model_runs": 0, "formal_business_acceptance_completed": False, "windows_task_changed": False}
    (ROOT/"evidence/knowledge-update/dated-service-scope-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": report["passed"], "checks": checks}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

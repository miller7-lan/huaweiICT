"""Verify literal municipal table evidence and scoped national source review."""

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_fetch import article_section, parse_html, select_articles, worker_once
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对告知承诺表格原文和国家条文复核，不批准地方流程或发布")
    parser.add_argument("--review-packet",required=True,type=Path)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update",read_only=True)
    local = read(ROOT / "evaluation/knowledge/zhengzhou-household-undertaking-facts-20261017.json")
    national = read(ROOT / "evaluation/knowledge/national-operational-baseline-review-20261017.json")
    private = store.root / "source-research/zhengzhou-consent-20261017"
    before = read(private / "before-main-data.json")
    before_national = read(private / "before-national-norm.json")
    readiness = read(ROOT / "evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    holdout = read(args.review_packet)["holdout_design_draft"]
    observed = national["subsequent_source_observation"]
    if set(holdout["stale_source_keys"]) != {"NATIONAL","F18",observed["holdout_alias"]}:
        parser.error("回执限于本阶段两项复核及一项观察变化的旧绑定失效状态；不覆盖后续阶段记录")
    checks = {}
    with store.db() as db:
        local_row,_ = store._verify(db,local["revision"],require_review=False)
        _,local_chunks = store._content(db,local["revision"])
        row,digest = store._verify(db,national["revision"],require_review=True)
        _,chunks = store._content(db,row["id"])
        _,old_chunks = store._content(db,national["previous_revision"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(national["source_id"],)).fetchone()[0])
        local_config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(local["source_id"],)).fetchone()[0])
        gates = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]}
        latest_states = dict(db.execute("SELECT state,count(*) FROM revisions WHERE rowid IN (SELECT max(rowid) FROM revisions GROUP BY document_id) GROUP BY state").fetchall())
        pending = [json.loads(r[0]) for r in db.execute("SELECT metadata FROM revisions WHERE state='pending_review' AND rowid IN (SELECT max(rowid) FROM revisions GROUP BY document_id)")]
        watch_binding = national["unresolved_window_watch"]
        watch_row,_ = store._verify(db,watch_binding["revision"],require_review=False)
        _,watch_chunks = store._content(db,watch_row["id"])
        watch_config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(watch_binding["source_id"],)).fetchone()[0])
        watch_job = db.execute("SELECT state,attempts,error FROM jobs WHERE site_id=?",(watch_binding["source_id"],)).fetchone()
        observed_row,_ = store._verify(db,observed["revision"],require_review=False)
        observed_old,_ = store._verify(db,observed["previous_revision"],require_review=False)
        _,observed_chunks = store._content(db,observed_row["id"])
        _,observed_old_chunks = store._content(db,observed_old["id"])
    local_raw = (store.root/"originals"/local_row["sha256"][:2]/local_row["sha256"]).read_bytes()
    pdf = PdfReader(store.root/"originals"/local_row["sha256"][:2]/local_row["sha256"],strict=True)
    native = [p.extract_text() or "" for p in pdf.pages]
    checks["independent_publisher_copy_normally_acquired"] = (local["receipt"]["status"] == 200 and local["receipt"]["normal_fetcher"] is True and fingerprint(local_raw) == local["original_sha256"])
    denied = read(private/"publisher-page-receipt.json")
    checks["denied_department_origin_not_retried"] = (denied["detail"] == {"phase":"robots","http_status":403}
        and denied["retried"] is False and local["receipt"]["source_is_not_the_denied_department_origin"] is True)
    checks["only_selected_pages_have_visual_review_claim"] = len(native) == 18 and local["visually_reviewed_pages"] == [1,2]
    checks["selected_native_spans_reproduce_exact_source"] = len(local_chunks) == len(local["selected_spans"]) == 3 and all(
        span["locator"] == chunk["locator"] and fingerprint(native[span["pdf_page"]-1].encode()) == span["parent_native_text_sha256"]
        and native[span["pdf_page"]-1][span["start"]:span["end"]] == chunk["text"] and fingerprint(chunk["text"].encode()) == span["span_sha256"]
        for span,chunk in zip(local["selected_spans"],local_chunks,strict=True))
    norm = lambda text:re.sub(r"\s+","",text)
    checks["row_10_joins_matter_proof_and_on_site_check"] = norm(local_chunks[2]["text"]) == "10郑州市公安局换领、补领居民身份证居民户口簿现场核查"
    row11 = native[1][native[1].index("\n11 ")+1:native[1].index("\n12 ")]
    checks["adjacent_no_check_field_is_not_transferred"] = ("免予核查" in row11 and "免予核查" not in local_chunks[2]["text"]
        and fingerprint(row11.encode()) == local["assessment"]["neighbor_guard"]["native_span_sha256"])
    extraction = json.loads(local_row["metadata"])["source_observation"]["pdf_extraction"]
    checks["selected_visual_observation_is_hash_bound"] = extraction["visual_page_png_sha256"] == [fingerprint((private/f"consent-list-page-{i}.png").read_bytes()) for i in [1,2]] and extraction["other_pages_not_reviewed"] is True
    local_meta = json.loads(local_row["metadata"])
    checks["local_currentness_and_procedure_remain_pending"] = (local_row["state"] == "pending_review" and local_meta["effective_from"] is None
        and local_meta["published_at"] is None and local_meta["scope_confirmed"] is False and local_meta["source_fact_assessment"]["business_approval"] is False and local_config["enabled"] is False)
    raw = (store.root/"originals"/row["sha256"][:2]/row["sha256"]).read_bytes()
    parsed,_ = parse_html(raw,config["content_selector"],min_text_length=1,excluded_selectors=config.get("excluded_content_selectors"))
    section,_ = article_section(parsed,config.get("article_start_title"))
    checks["national_selection_matches_actual_worker_parser"] = chunks == select_articles(section,config["include_articles"],config.get("article_categories")) == [x for x in old_chunks if x["locator"] != "第七十六条"] and len(chunks) == 10
    metadata = json.loads(row["metadata"])
    checks["national_review_and_explicit_date_are_bound"] = (digest == national["review_digest"] and metadata["effective_from"] == "2021-10-01"
        and "本规范自2021年10月1日起施行" in norm(chunks[-1]["text"]) and not metadata.get("date_basis")
        and metadata["source_fact_assessment"] == national["assessment"] and national["human_review_completed"] is False)
    checks["portal_observation_unchanged_by_manual_review"] = metadata["source_observation"] == json.loads(before_national["revision"]["metadata"])["source_observation"]
    checks["unresolved_window_retained_in_history"] = any(c["locator"] == "第七十六条" for c in old_chunks) and not any(c["locator"] == "第七十六条" for c in chunks)
    checks["original_scope_is_preserved_by_live_window_watch"] = (watch_chunks == [x for x in old_chunks if x["locator"] == "第七十六条"]
        and watch_config["enabled"] is True and watch_job["state"] == "done" and watch_job["attempts"] == 1 and watch_job["error"] is None
        and watch_row["sha256"] == row["sha256"])
    checks["window_watch_is_not_a_second_policy_or_approval"] = (watch_row["state"] == "pending_review"
        and json.loads(watch_row["metadata"])["scope_confirmed"] is False
        and watch_config["metadata"]["monitor_partition"]["parent_source_id"] == national["source_id"]
        and watch_binding["separate_policy_count_increased"] is False)
    observed_meta = json.loads(observed_row["metadata"])
    observed_old_meta = json.loads(observed_old["metadata"])
    observed_fields = observed_meta["source_observation"]["fields"]
    old_fields = observed_old_meta["source_observation"]["fields"]
    checks["natural_observation_change_remains_pending_and_stales_window_binding"] = (
        observed_row["sha256"] == observed_old["sha256"] == observed["original_sha256"]
        and observed_chunks == observed_old_chunks and observed_row["state"] == "pending_review"
        and {k:v for k,v in observed_meta.items() if k != "source_observation"} == {k:v for k,v in observed_old_meta.items() if k != "source_observation"}
        and observed_fields == dict(old_fields, **{observed["added_field"]:observed["literal_values"]})
        and observed["added_field"] not in old_fields and observed_meta["scope_confirmed"] is False)
    checks["policy_and_formal_gates_preserved"] = fingerprint((store.root/"publication-policy.json").read_bytes()) == before["policy_sha256"] and gates == dict(before["gates"],reviews=before["gates"]["reviews"]+1)
    checks["actual_export_exposes_scoped_review_and_stale_bindings"] = (readiness["recorded_reviewed_source_count"] == 4
        and len(readiness["reviewed_operational_norms"]) == 1 and readiness["reviewed_operational_norms"][0]["revision"] == row["id"]
        and holdout["live_binding_checks"]["sources_match_latest"] is False and holdout["automatic_approval"] is False)
    backup = read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"],read_only=True)
    with restored.db() as db:
        copy,copy_digest = restored._verify(db,row["id"],require_review=True)
        local_copy,_ = restored._verify(db,local_row["id"],require_review=False)
    checks["full_restore_preserves_review_and_local_pending_evidence"] = copy_digest == digest and copy["metadata"] == row["metadata"] and local_copy["metadata"] == local_row["metadata"] and backup["restore"]["services_started"] is False
    # Reproduce collection with the real restored configuration; no network or model.
    replay = restore_bundle(backup["backup"]["directory"],ROOT/".runtime/knowledge-update-restores"/("national-baseline-"+uuid.uuid4().hex[:10]))
    clone = KnowledgeStore(replay["restored_to"])
    class Fixture:
        def __init__(self,payload):self.payload=payload
        def fetch(self,c,url):
            assert c["id"] == national["source_id"] and url == national["url"]
            return 200,{"content-type":"text/html"},self.payload,url
    def run(payload):
        with clone.db() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 0
            db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=?",(national["source_id"],))
        worker_once(clone,fetcher=Fixture(payload),owner="isolated-national-baseline-replay")
        with clone.db() as db:
            return db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1",(national["source_id"],)).fetchone()
    same = run(raw)
    checks["same_body_replay_preserves_recorded_review"] = same["id"] == row["id"] and same["state"] == "reviewed"
    modified = raw.replace("居民户口簿".encode(),"测试材料".encode());assert modified != raw
    changed = run(modified)
    checks["synthetic_body_change_still_requires_review"] = changed["id"] != row["id"] and changed["state"] == "pending_review" and json.loads(changed["metadata"])["effective_from"] is None
    result = {"schema":1,"captured_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
        "local_pending_revision":local_row["id"],"national_reviewed_revision":row["id"],"counts":store.status()["counts"],
        "latest_states":latest_states,"pending_without_effective_from":sum(not m.get("effective_from") for m in pending),
        "formal_gates":gates,"draft_stale_sources":holdout["stale_source_keys"],"new_model_runs":0,"replay_network_requests":0,
        "synthetic_replay_is_not_a_new_policy":True,"business_acceptance_completed":False,"local_currentness_approved":False}
    (ROOT/"evidence/knowledge-update/undertaking-national-baseline-integrity.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"passed":result["passed"],"checks":checks,"latest_states":latest_states},ensure_ascii=False))
    if not result["passed"]:raise SystemExit(1)


if __name__ == "__main__":
    main()

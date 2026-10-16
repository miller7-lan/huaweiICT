"""Verify original fee clauses, background classification and unresolved PDF quality."""
import argparse
import json
import re
import uuid
from datetime import datetime,timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore,fingerprint
from gov_mcp.knowledge_backup import restore_bundle
from gov_mcp.knowledge_fetch import observe_html,parse_html,select_paragraph_groups,select_paragraph_keywords,worker_once
from gov_mcp.knowledge_pdf import extract_document
from nexent_api import ROOT,RUNTIME


def read(path):return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser=argparse.ArgumentParser(description="核对收费原文、背景排除及困难减免待核质量；不执行模型或发布")
    parser.add_argument("--review-packet",type=Path,required=True)
    args=parser.parse_args()
    source=read(ROOT/"evaluation/knowledge/national-fee-foundation-review-20261017.json")
    store=KnowledgeStore(RUNTIME/"knowledge-update",read_only=True)
    private=store.root/"source-research/fee-currentness-20261017"
    before=read(private/"before-main.json")
    readiness=read(ROOT/"evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes())==readiness["review_packet_sha256"]
    with store.db() as db:
        row,digest=store._verify(db,source["revision"],require_review=True)
        latest=db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1",(row["document_id"],)).fetchone()[0]
        _,chunks=store._content(db,row["id"])
        metadata=json.loads(row["metadata"])
        site=db.execute("SELECT * FROM sites WHERE id=?",(source["source_id"],)).fetchone();config=json.loads(site["config"])
        job=db.execute("SELECT * FROM jobs WHERE site_id=?",(source["source_id"],)).fetchone()
        gates={t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]}
        if latest!=row["id"] or gates!=dict(before["gates"],reviews=before["gates"]["reviews"]+1):parser.error("来源或闸门有后续变化，不覆盖本阶段回执")
        if job is None or job["state"] in ("queued","running"):parser.error("已排队正常复查尚未终结；查看同一作业，不重复请求")
        backgrounds=[]
        for binding in source["background_dispositions"]:
            store._verify_exclusion(db,binding["revision"])
            r,c=store._content(db,binding["revision"])
            scfg=json.loads(db.execute("SELECT config FROM sites WHERE id=?",(binding["source_id"],)).fetchone()[0])
            backgrounds.append((r,c,scfg))
        waiver,_=store._verify(db,source["waiver_quality_revision"],require_review=False);_,waiver_chunks=store._content(db,waiver["id"])
        waiver_config=json.loads(db.execute("SELECT config FROM sites WHERE id='FEE-WAIVER-2004-ZJ-LEAD'").fetchone()[0])
        stopped={sid:dict(db.execute("SELECT * FROM sites WHERE id=?",(sid,)).fetchone()) for sid in before["stopped_sources"]}
        fee2018,digest2018=store._verify(db,source["assessment"]["basis"]["first_issue_reviewed_revision"],require_review=True)
    original=(store.root/"originals"/row["sha256"][:2]/row["sha256"]).read_bytes()
    parsed,_=parse_html(original,config["content_selector"])
    checks={
        "normal_government_copies_have_exact_receipts":all(read(private/(name+"-receipt.json"))["status"]==200 and read(private/(name+"-receipt.json"))["normal_fetcher"] is True and fingerprint((private/(name+suffix)).read_bytes())==read(private/(name+"-receipt.json"))["sha256"] for name,suffix in [("qg-fee-2003",".html"),("sc-fee-2003",".pdf"),("tangyin-fee",".html")]),
        "review_digest_and_ai_scope_are_bound":digest==source["review_digest"] and metadata["source_fact_assessment"]==source["assessment"] and source["human_review_completed"] is False,
        "real_group_configuration_selects_complete_original_paragraphs":chunks==select_paragraph_groups(parsed,config["include_paragraph_keyword_groups"]) and [c["locator"] for c in chunks]==["正文段落3","正文段落4","正文段落7","正文段落8"],
        "commencement_is_explicit_and_republication_not_substituted":metadata["effective_from"]=="2004-01-01" and "2004年1月1日" in chunks[-1]["text"] and metadata["published_at"] is None and metadata["document_signed_at"]=="2003-12-30" and metadata["source_observation"]["fields"]["meta:pubdate"]==["2025-12-24"],
        "first_issue_suspension_review_is_preserved":digest2018==source["assessment"]["basis"]["first_issue_review_digest"] and fee2018["state"]=="reviewed",
        "real_background_watch_recheck_preserves_review":job["state"]=="done" and job["attempts"]==1 and job["error"] is None and config["enabled"] is True and site["paused_reason"] is None and site["last_success"] is not None,
        "previously_stopped_sources_are_not_resumed":all(stopped[sid]["config"]==old["config"] and stopped[sid]["paused_reason"]==old["paused_reason"] for sid,old in before["stopped_sources"].items()),
    }
    sc,sc_chunks,scfg=backgrounds[0];ty,ty_chunks,tycfg=backgrounds[1]
    checks["independent_pdf_copy_is_reference_not_another_rule"]=sc_chunks==extract_document(private/"sc-fee-2003.pdf")["chunks"] and sc["state"]=="excluded" and scfg["enabled"] is False and len(sc_chunks)==2
    norm=lambda s:re.sub(r"\s+","",s)
    sc1,sc2=map(lambda c:norm(c["text"]),sc_chunks)
    checks["financial_and_commencement_clauses_match_pdf_without_rewriting_spelling"]=(norm(chunks[0]["text"])[norm(chunks[0]["text"]).index("因此，同意"):]==sc1[sc1.index("因此，同意"):sc1.index("公安机关为居民办理临时")]
        and norm(chunks[1]["text"])==sc1[sc1.index("公安机关为居民办理临时"):sc1.index("二、")]
        and norm(chunks[2]["text"])==sc2[sc2.index("四、"):sc2.index("五、")]
        and norm(chunks[3]["text"])==sc2[sc2.index("五、"):sc2.index("国家发展改革委")]
        and "聚酯" in chunks[0]["text"] and "聚脂" in sc1)
    checks["both_pdf_pages_have_bound_visual_observation"]=source["assessment"]["basis"]["sc_pdf_visual_pages"]==[1,2] and source["assessment"]["basis"]["sc_png_sha256"]==[fingerprint((private/f"sc-fee-2003-page-{i}.png").read_bytes()) for i in [1,2]]
    ty_raw=(private/"tangyin-fee.html").read_bytes();ty_parsed,_=parse_html(ty_raw,tycfg["content_selector"])
    cells=json.loads(ty["metadata"])["source_fact_assessment"]["table_cells"]
    checks["other_county_directory_has_citations_not_invented_amounts"]=ty["state"]=="excluded" and tycfg["enabled"] is False and json.loads(ty["metadata"])["region"]=="汤阴县" and ty_chunks==select_paragraph_keywords(ty_parsed,tycfg["include_paragraph_keywords"],1) and len(cells)==8 and cells[6]==cells[7]=="财综〔2007〕34号，财税〔2018〕37号" and not re.search(r"(?:20|40|10)元",ty_chunks[0]["text"])
    wm=json.loads(waiver["metadata"]);quality=source["waiver_quality_note"]
    native=(store.root/"source-research/fees-20261017/waiver-2004-page-1-native.txt").read_text(encoding="utf-8")
    checks["waiver_native_visual_difference_is_recorded_without_text_correction"]=waiver_chunks==before["waiver_chunks"] and waiver["body_sha256"]==before["waiver"]["body_sha256"] and wm["extraction_quality_notes"]==[quality] and native[quality["start"]:quality["end"]]==quality["native_literal"]=="观" and quality["visual_reading"]=="现" and fingerprint(native.encode())==quality["parent_native_text_sha256"] and quality["start"]<native.index("一、")
    checks["hardship_eligibility_and_blank_page_parser_remain_unapproved"]=waiver["state"]=="pending_review" and wm["scope_confirmed"] is False and wm["source_fact_assessment"]["business_approval"] is False and waiver_config["enabled"] is False
    try:extract_document(store.root/"source-research/fees-20261017/waiver-2004.pdf")
    except ValueError:blank_rejected=True
    else:blank_rejected=False
    checks["hardship_eligibility_and_blank_page_parser_remain_unapproved"] &= blank_rejected
    holdout=read(args.review_packet)["holdout_design_draft"]
    checks["draft_and_formal_gates_preserved"]=all(fingerprint((ROOT/p).read_bytes())==h for p,h in before["untouched_files"].items()) and set(holdout["stale_source_keys"])=={"NATIONAL","F18","WINDOW"} and holdout["automatic_approval"] is False and fingerprint((store.root/"publication-policy.json").read_bytes())==before["policy_sha256"] and gates==dict(before["gates"],reviews=8)
    exported={d["source_id"]:d for d in read(ROOT/"data/knowledge/candidate-catalog.json")["documents"]}
    checks["actual_export_preserves_new_review_and_pending_quality_notes"]=readiness["recorded_reviewed_source_count"]==6 and exported[source["source_id"]]["revision"]==row["id"] and exported["FEE-WAIVER-2004-ZJ-LEAD"]["metadata"]["extraction_quality_notes"]==[quality]
    backup=read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored=restore_bundle(backup["backup"]["directory"],ROOT/".runtime/knowledge-update-restores"/("fee-foundation-"+uuid.uuid4().hex[:10]));clone=KnowledgeStore(restored["restored_to"])
    with clone.db() as db:
        copied,copied_digest=clone._verify(db,row["id"],require_review=True)
        quality_copy,_=clone._verify(db,waiver["id"],require_review=False)
        for binding in source["background_dispositions"]:clone._verify_exclusion(db,binding["revision"])
    checks["full_restore_preserves_review_exclusions_and_quality_notes"]=copied_digest==digest and copied["metadata"]==row["metadata"] and quality_copy["metadata"]==waiver["metadata"] and restored["services_started"] is False
    try:clone.candidate("郑州市二七区","demo-id-renewal")
    except ValueError as error:candidate_error=str(error)
    else:candidate_error=None
    checks["remaining_unreviewed_evidence_still_blocks_candidate"]=candidate_error=="Unreviewed or changed content"
    class Fixture:
        def __init__(self,payload):self.payload=payload
        def fetch(self,c,url):
            assert c["id"]==source["source_id"] and url==source["url"]
            return 200,{"content-type":"text/html"},self.payload,url
    def replay(payload):
        with clone.db() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0]==0
            assert db.execute("UPDATE jobs SET state='queued',attempts=0,due=0,lease_until=NULL WHERE site_id=?",(source["source_id"],)).rowcount==1
        worker_once(clone,fetcher=Fixture(payload),owner="isolated-fee-foundation-replay")
        with clone.db() as db:return db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? ORDER BY r.rowid DESC LIMIT 1",(source["source_id"],)).fetchone()
    same=replay(original);checks["same_body_replay_preserves_review"]=same["id"]==row["id"] and same["state"]=="reviewed"
    changed_raw=original.replace("每证20元".encode(),"每证21元".encode());assert changed_raw!=original
    changed=replay(changed_raw);cm=json.loads(changed["metadata"])
    checks["real_selection_still_captures_synthetic_amount_change_and_withholds_approval"]=changed["id"]!=row["id"] and changed["state"]=="pending_review" and cm["effective_from"] is None and cm["published_at"] is None
    result={"schema":1,"captured_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,"revision":row["id"],"counts":store.status()["counts"],"formal_gates":gates,"draft_stale_sources":holdout["stale_source_keys"],"candidate_error":candidate_error,"new_model_runs":0,"replay_network_requests":0,"synthetic_change_is_not_a_policy":True,"business_acceptance_completed":False,"windows_task_touched":False}
    (ROOT/"evidence/knowledge-update/fee-foundation-integrity.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"passed":result["passed"],"checks":checks},ensure_ascii=False))
    if not result["passed"]:raise SystemExit(1)


if __name__=="__main__":main()

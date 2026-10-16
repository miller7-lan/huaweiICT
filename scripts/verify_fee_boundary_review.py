"""Check the fee-clause review and pending original, without approving a release."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import parse_html, select_paragraph_keywords
from gov_mcp.knowledge_pdf import checked_text, extract_document
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对收费分支资料，不执行模型、改写草案或发布")
    parser.add_argument("--review-packet",required=True,type=Path)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update",read_only=True)
    report = read(ROOT / "evaluation/knowledge/fee-first-issue-and-waiver-review-20261017.json")
    private = store.root / "source-research/fees-20261017"
    before = read(private / "before-main-data.json")
    readiness = read(ROOT / "evidence/knowledge-update/review-readiness.json")
    assert fingerprint(args.review_packet.read_bytes()) == readiness["review_packet_sha256"]
    packet = read(args.review_packet)
    holdout = packet["holdout_design_draft"]
    if holdout["stale_source_keys"] != ["F18"]:
        parser.error("本回执绑定首次申领复核后、F18迁移前的阶段；不覆盖历史阶段结果")
    with store.db() as db:
        row,digest = store._verify(db,report["first_issue"]["revision"],require_review=True)
        _,chunks = store._content(db,row["id"])
        metadata = json.loads(row["metadata"])
        waiver,_ = store._verify(db,report["hardship_original"]["revision"],require_review=False)
        waiver_meta = json.loads(waiver["metadata"])
        _,waiver_chunks = store._content(db,waiver["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(report["first_issue"]["source_id"],)).fetchone()[0])
        waiver_config = json.loads(db.execute("SELECT config FROM sites WHERE id=?",(report["hardship_original"]["source_id"],)).fetchone()[0])
        gates = {t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["formal_gates"]}
        original_configs_preserved = all(db.execute("SELECT config FROM sites WHERE id=?",(k,)).fetchone()[0] == v for k,v in before["source_configs"].items())
        states = dict(db.execute("SELECT state,count(*) FROM revisions WHERE rowid IN (SELECT max(rowid) FROM revisions GROUP BY document_id) GROUP BY state").fetchall())
        pending = [json.loads(r[0]) for r in db.execute("SELECT metadata FROM revisions WHERE state='pending_review' AND rowid IN (SELECT max(rowid) FROM revisions GROUP BY document_id)")]
    raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
    parsed,_ = parse_html(raw,config["content_selector"],min_text_length=8)
    selected = select_paragraph_keywords(parsed,config["include_paragraph_keywords"],config["paragraph_expected_count"])
    original = report["hardship_original"]
    pdf_path = store.root / "originals" / waiver["sha256"][:2] / waiver["sha256"]
    pdf = PdfReader(pdf_path,strict=True)
    native = [p.extract_text() or "" for p in pdf.pages]
    try:
        extract_document(pdf_path)
    except ValueError as error:
        ordinary_parser_refused_blank = str(error) == "Missing text layer; explicit OCR source configuration required"
    else:
        ordinary_parser_refused_blank = False
    checks = {
        "review_and_original_are_bound":digest == report["first_issue"]["review_digest"] and fingerprint(raw) == before["first_issue_revision"]["sha256"],
        "complete_first_clause_matches_canonical_parser":chunks == selected and len(chunks) == 1,
        "legal_start_comes_from_selected_clause":metadata["effective_from"] == "2018-04-01" and chunks[0]["text"] == "一、自2018年4月1日起，停征首次申领居民身份证工本费。",
        "ambiguous_publication_is_not_fabricated":metadata["published_at"] is None and metadata["document_signed_at"] == "2018-04-12" and not metadata.get("date_basis"),
        "source_observation_is_immutable":metadata["source_observation"] == json.loads(before["first_issue_revision"]["metadata"])["source_observation"],
        "scoped_ai_review_is_not_a_renewal_fee_waiver":metadata["source_fact_assessment"] == report["first_issue"]["assessment"] and report["human_review_completed"] is False and metadata["source_fact_assessment"]["current_applicability"] == "national_first_issue_fee_suspension_only_not_renewal_waiver",
        "new_original_was_normally_acquired":original["receipt"]["normal_fetcher"] is True and original["receipt"]["status"] == 200 and fingerprint(pdf_path.read_bytes()) == original["original_sha256"],
        "native_body_and_blank_second_page_are_exact":len(native) == 2 and not native[1].strip() and len(waiver_chunks) == 1 and waiver_chunks[0]["text"] == checked_text(native[0]) and fingerprint(waiver_chunks[0]["text"].encode()) == original["native_text_sha256"],
        "all_pages_visual_observation_is_hash_bound":[fingerprint((private/f"waiver-2004-page-{i}.png").read_bytes()) for i in [1,2]] == original["visual_page_png_sha256"] and original["blank_page_visually_checked"] == 2,
        "ordinary_pdf_parser_was_not_weakened":ordinary_parser_refused_blank,
        "hardship_current_applicability_is_still_pending":waiver["state"] == "pending_review" and waiver_meta["source_fact_assessment"]["business_approval"] is False and waiver_meta["scope_confirmed"] is False,
        "manual_pdf_source_is_not_automatically_collected":waiver_config["enabled"] is False,
        "existing_source_configs_and_policy_preserved":original_configs_preserved and fingerprint((store.root/"publication-policy.json").read_bytes()) == before["policy_sha256"],
        "one_new_review_without_formal_release":gates == dict(before["formal_gates"],reviews=before["formal_gates"]["reviews"]+1),
        "F18_staleness_is_visible_without_approval":holdout["live_binding_checks"]["sources_match_latest"] is False and holdout["automatic_approval"] is False,
        "readiness_exposes_scoped_fee_review":readiness["recorded_reviewed_source_count"] == 3 and len(readiness["reviewed_fee_clauses"]) == 1 and readiness["reviewed_fee_clauses"][0]["revision"] == row["id"],
    }
    integrity = read(ROOT / "evidence/knowledge-update/holdout-draft-integrity.json")
    failed = sorted(k for k,v in integrity["checks"].items() if not v)
    checks["draft_verifier_withholds_current_integrity"] = integrity["draft_integrity_passed"] is False and failed == sorted(["latest_originals_and_literal_anchor_spans_match","actual_export_contains_both_drafts_and_preserves_empty_approvals"])
    prior = read(ROOT / "evidence/knowledge-update/archive/holdout-v3-bound-integrity-20261017.json")
    checks["prior_bound_v3_receipt_preserved"] = prior["draft_integrity_passed"] is True and prior["draft_sha256"] == holdout["reference_draft"]["content_sha256"]
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"],read_only=True)
    with restored.db() as db:
        copied,copied_digest = restored._verify(db,row["id"],require_review=True)
        copied_waiver,_ = restored._verify(db,waiver["id"],require_review=False)
    checks["full_restore_preserves_review_and_pending_original"] = copied_digest == digest and copied["metadata"] == row["metadata"] and copied_waiver["metadata"] == waiver["metadata"] and copied_waiver["state"] == "pending_review" and backup["restore"]["services_started"] is False
    result = {"schema":1,"captured_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
        "first_issue_reviewed_revision":row["id"],"hardship_pending_revision":waiver["id"],"counts":store.status()["counts"],
        "latest_states":states,"pending_without_effective_from":sum(not m.get("effective_from") for m in pending),
        "formal_gates":gates,"draft_stale_sources":holdout["stale_source_keys"],"expected_draft_failed_checks":failed,
        "new_model_runs":0,"reference_frozen":False,"independence_certified":False,"business_acceptance_completed":False}
    (ROOT / "evidence/knowledge-update/fee-boundary-review-integrity.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"passed":result["passed"],"checks":checks,"states":states,"pending_without_effective_from":result["pending_without_effective_from"]},ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

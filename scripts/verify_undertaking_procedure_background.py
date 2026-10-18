"""Audit the normally archived procedural background and its answer-corpus exclusion."""
import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import observe_html, parse_html, select_paragraph_groups
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    private = store.root/"source-research/henan-undertaking-procedure-20261018"
    report = read(ROOT/"evaluation/knowledge/henan-undertaking-procedure-background-20261018.json")
    before = read(private/"before-main.json")
    backup = read(ROOT/"evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    receipt = read(private/"receipt.json")
    raw = (private/"provincial-procedure.html").read_bytes()
    with store.db() as db:
        row,_ = store._verify(db, report["revision"], require_review=False)
        _,chunks = store._content(db, row["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (report["source_id"],)).fetchone()[0])
        metadata = json.loads(row["metadata"])
        paragraphs,_ = parse_html(raw, config["content_selector"])
        selected = select_paragraph_groups(paragraphs, report["paragraph_keyword_groups"])
        configs = {r["id"]:fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")}
        checks = {
            "report_digest_valid":fingerprint({k:v for k,v in report.items() if k != "content_sha256"}) == report["content_sha256"],
            "normal_http_original_matches_archive_and_receipt":receipt == report["receipt"] and receipt["normal_fetcher"] is True and receipt["status"] == 200 and receipt["bytes"] == len(raw) and fingerprint(raw) == row["sha256"] == report["original_sha256"] and (store.root/"originals"/row["sha256"][:2]/row["sha256"]).read_bytes() == raw,
            "seven_complete_unique_paragraphs_reproduce_source":chunks == selected and len(chunks) == 7 and report["selected_paragraphs"] == [{"locator":c["locator"], "text_sha256":fingerprint(c["text"].encode()), "characters":len(c["text"])} for c in chunks],
            "definition_exclusions_choice_workflow_checks_and_withdrawal_retained":[c["locator"] for c in chunks] == ["正文段落9", "正文段落10", "正文段落11", "正文段落12", "正文段落13", "正文段落14", "正文段落16"],
            "unselected_credit_and_organization_clauses_not_in_answer_selection":len(paragraphs) == 22 and all(c["locator"] not in {"正文段落15", "正文段落17", "正文段落18", "正文段落19", "正文段落20", "正文段落21", "正文段落22"} for c in chunks),
            "signature_publication_and_unknown_commencement_separated":paragraphs[3]["text"] == "2021年1月8日" and metadata["document_signed_at"] == "2021-01-08" and report["page_observation"]["fields"]["meta:pubdate"] == ["2021-01-15"] and metadata["published_at"] is None and metadata["effective_from"] is None and metadata["effective_to"] is None and report["first_publication_verified"] is False,
            "page_observations_reproduce_without_rewriting_empty_source_field":metadata["source_observation"] == report["page_observation"] == observe_html(raw, content_selector=config["content_selector"]) and metadata["source_observation"]["fields"]["meta:contentsource"] == [""],
            "background_is_excluded_and_not_approved_or_monitored":row["state"] == "excluded" and config["enabled"] is False and metadata["collection_role"] == "governance_reference" and metadata["scope_confirmed"] is False and metadata["source_fact_assessment"]["business_approval"] is False and metadata["source_fact_assessment"]["current_applicability"] == "unknown" and db.execute("SELECT reviewer FROM exclusions WHERE revision_id=?", (row["id"],)).fetchone()[0] == "Codex AI",
            "all_preexisting_source_configs_unchanged":set(configs) == set(before["configs"]) | {report["source_id"]} and all(configs[key] == sha for key,sha in before["configs"].items()),
            "drafts_and_scope_preserved":all(fingerprint((ROOT/path).read_bytes()) == sha for path,sha in before["file_sha256"].items()),
            "business_review_evaluation_and_publication_gates_unchanged":{t:db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in before["gates"]} == before["gates"],
        }
    with restored.db() as db:
        copied,_ = restored._verify(db, report["revision"], require_review=False)
        _,copied_chunks = restored._content(db, copied["id"])
        checks["complete_restore_preserves_new_original_metadata_exclusion_and_selection"] = copied["sha256"] == row["sha256"] and copied["metadata"] == row["metadata"] and copied["state"] == "excluded" and copied_chunks == chunks and backup["passed"] is True
    pdf_attempt = read(store.root/"source-research/zhengzhou-procedure-20261018/receipt.json")
    checks["denied_municipal_pdf_not_claimed_as_acquired"] = pdf_attempt["stop_detail"] == {"phase":"robots", "http_status":403} and pdf_attempt["body_acquired"] is False and not (store.root/"source-research/zhengzhou-procedure-20261018/municipal-procedure.pdf").exists()
    try:
        store.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        checks["unresolved_materials_still_block_formal_candidate"] = str(error) == "Unreviewed or changed content"
    else:
        checks["unresolved_materials_still_block_formal_candidate"] = False
    result = {"schema":1, "captured_at":datetime.now(timezone.utc).isoformat(), "passed":all(checks.values()), "checks":checks,
        "counts":store.status()["counts"], "new_normally_archived_originals":1, "selected_paragraphs":7,
        "business_approvals":0, "model_requests":0, "business_publications":0,
        "limits":"Provenance and general procedure context, not current Erqi material applicability or a visitor answer release."}
    (ROOT/"evidence/knowledge-update/undertaking-procedure-background-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

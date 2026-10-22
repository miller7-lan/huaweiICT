"""Verify an independent government-hosted webprint PDF without approving local eligibility."""
import json
import re
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_pdf import extract_document
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def item_spans(text):
    starts = [re.search(r"(?m)^[ \t]*(" + marker + r")", text).start(1) for marker in ["一、", "二、", "三、", "四、", "五、"]]
    starts.append(text.index("中华人民共和国财政部", starts[-1]))
    return [{"item": i + 1, "start": start, "end": starts[i + 1] - (len(text[start:starts[i + 1]]) - len(text[start:starts[i + 1]].rstrip())), "text": text[start:starts[i + 1]].rstrip()} for i, start in enumerate(starts[:-1])]


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    private = store.root / "source-research/fee-waiver-langxi-20261018"
    before = read(private / "before-main.json")
    report = read(ROOT / "evaluation/knowledge/fee-waiver-official-copy-20261018.json")
    expected = dict(before["counts"])
    for key, value in {"sites": 1, "documents": 1, "revisions": 2, "chunks": 6, "latest_chunks": 3}.items():
        expected[key] += value
    if store.status()["counts"] != expected:
        raise SystemExit("主库已有后续更新或数量差异；保留减免副本阶段回执，先核对当前状态")
    receipts = read(private / "fetch-receipts.json")
    parent, raw = (private / "parent.html").read_bytes(), (private / "fee-waiver.pdf").read_bytes()
    parsed = extract_document(private / "fee-waiver.pdf")
    page = parsed["chunks"][0]["text"]
    items = item_spans(page)
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    with store.db() as db:
        row, _ = store._verify(db, report["revision"], require_review=False)
        store._verify_exclusion(db, row["id"])
        _, chunks = store._content(db, row["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (report["source_id"],)).fetchone()[0])
        metadata = json.loads(row["metadata"])
        old, _ = store._verify(db, report["comparison"]["prior_revision"], require_review=False)
        _, old_chunks = store._content(db, old["id"])
        old_items = item_spans(old_chunks[0]["text"])
        configs = {r["id"]: fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")}
        old_revisions = {r["id"]: fingerprint(dict(r)) for r in db.execute("SELECT * FROM revisions WHERE id NOT IN (?,?)", (row["id"], report["initial_revision"]))}
        gates = {t: [fingerprint(dict(r)) for r in db.execute("SELECT * FROM " + t)] for t in before["gates"]}
        first, _ = store._verify(db, "rev-e41c61067fe70ce4ce221dbf")
        _, first_chunks = store._content(db, first["id"])
    fold = lambda value: re.sub(r"\s+", "", value)
    selected = [items[i] for i in [0, 1, 4]]
    catalog = next(r for r in read(ROOT / "data/knowledge/candidate-catalog.json")["documents"] if r["source_id"] == report["source_id"])
    checks = {
        "report_digest_and_private_receipt_match": fingerprint({k: v for k, v in report.items() if k != "content_sha256"}) == report["content_sha256"] and {k: v for k, v in report.items() if k != "content_sha256"} == read(private / "archive-receipt.json"),
        "both_exact_linked_entries_normally_fetched_once": receipts == report["normal_fetch_receipts"] and len(receipts) == 2 and all(r["normal_fetcher"] is True and r["attempted_once"] is True and r["status"] == 200 and r["body_acquired"] is True for r in receipts) and receipts[0]["sha256"] == fingerprint(parent) and receipts[1]["sha256"] == fingerprint(raw) == row["sha256"] and len(raw) == receipts[1]["bytes"] == 813961,
        "parent_public_iframe_contains_the_exact_pdf_path": report["parent"]["iframe_src"] in parent.decode("utf-8") and report["url"].endswith("/file_xc/4/202403/202403046c77205b1c9546488e541d934f100c9f.pdf") and report["parent"]["iframe_src"].endswith("?file=/file_xc/4/202403/202403046c77205b1c9546488e541d934f100c9f.pdf"),
        "pdf_original_header_and_strict_single_page_extraction_match": raw.startswith(b"%PDF-") and (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes() == raw and len(parsed["chunks"]) == 1 and parsed["extraction"]["method"] == "pdf_text_layer" and fingerprint(page.encode()) == report["pdf_observation"]["native_page_text_sha256"],
        "all_source_text_ranges_reproduce_the_three_full_selected_items": [c["text"] for c in chunks] == [s["text"] for s in selected] and all(page[s["start"]:s["end"]] == s["text"] for s in selected) and report["selected_spans"] == [{k: s[k] for k in ["item", "start", "end"]} | {"text_sha256": fingerprint(s["text"].encode())} for s in selected],
        "five_items_match_prior_pdf_only_under_explicit_whitespace_fold": all(fold(new["text"]) == fold(prior["text"]) for new, prior in zip(items, old_items)) and old["sha256"] == report["comparison"]["prior_original_sha256"] and report["comparison"]["normalization"] == "whitespace_removal_only_no_glyph_or_punctuation_repair" and len(items) == len(old_items) == 5,
        "exemption_reduction_proof_and_renewal_conditions_preserved": "免收工本费" in chunks[0]["text"] and "减半征收工本费" in chunks[1]["text"] and all("初次申请领取和换领" in c["text"] and "有效证明" in c["text"] for c in chunks[:2]) and "2004年1月1日" in fold(chunks[2]["text"]),
        "webprint_dates_not_relabelled_as_current_legal_approval": metadata["document_type"] == "official_webprint_copy" and metadata["effective_from"] is None and metadata["published_at"] is None and report["dates"]["parent_page_date"] == "2024-03-04" and report["dates"]["printed_site_banner_date"] == "2022-01-14" and report["dates"]["document_stated_execution_date"] == "2004-01-01" and report["dates"]["first_publication_verified"] is False,
        "complete_render_is_bound_and_ai_visual_observation_attributed": fingerprint((private / "page-1.png").read_bytes()) == report["pdf_observation"]["page_png_sha256"] and report["pdf_observation"]["visual_pages_checked"] == [1] and report["reviewer_kind"] == "ai" and report["human_review_completed"] is False,
        "copy_is_excluded_disabled_and_not_an_additional_answer_rule": row["state"] == "excluded" and config["enabled"] is False and metadata["scope_confirmed"] is False and metadata["collection_role"] == "governance_reference" and metadata["source_fact_assessment"]["business_approval"] is False and catalog["state"] == "excluded" and catalog["metadata"]["document_type"] == "official_webprint_copy",
        "all_old_sources_revisions_reviews_drafts_and_policy_preserved": {k: v for k, v in configs.items() if k != report["source_id"]} == before["configs"] and old_revisions == before["revisions"] and gates == before["gates"] and all(fingerprint((ROOT / path).read_bytes()) == sha for path, sha in before["file_sha256"].items()),
        "first_issue_suspension_boundary_kept": any("首次申领" in c["text"] and "停征" in c["text"] for c in first_chunks) and first["state"] == "reviewed" and report["conclusions"]["first_issue_fee_reintroduced"] is False,
        "current_local_eligibility_and_original_glyph_difference_still_unresolved": all(value is False for value in report["conclusions"].values()) and old["state"] == "pending_review" and metadata["source_fact_assessment"]["current_applicability"] == "unknown",
    }
    try:
        extract_document(store.root / "source-research/fees-20261017/waiver-2004.pdf")
    except ValueError as error:
        checks["original_blank_page_parser_limit_not_weakened"] = str(error) == "Missing text layer; explicit OCR source configuration required"
    else:
        checks["original_blank_page_parser_limit_not_weakened"] = False
    with restored.db() as db:
        copied, _ = restored._verify(db, row["id"], require_review=False)
        restored._verify_exclusion(db, row["id"])
        _, copied_chunks = restored._content(db, row["id"])
    checks["full_restore_preserves_new_pdf_metadata_selection_and_exclusion"] = backup["passed"] is True and copied["sha256"] == row["sha256"] and copied["metadata"] == row["metadata"] and copied_chunks == chunks
    result = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks, "counts": store.status()["counts"], "new_normally_archived_pdf_copies": 1, "new_business_rules": 0, "business_approvals": 0, "business_publications": 0, "model_requests": 0, "limits": "Original/source-copy and literal-condition corroboration only; no current Erqi relief eligibility, proof-sharing procedure, glyph repair or new visitor answer rule."}
    (ROOT / "evidence/knowledge-update/fee-waiver-copy-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

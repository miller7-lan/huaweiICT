"""Verify draft separation, source spans and rejected writer output, never acceptance."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_review import development_registry, question_fingerprint, validate_attributed_review
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="核对草案结构和出处，不核发独立业务验收结论")
    parser.add_argument("--writer-directory",required=True,type=Path)
    parser.add_argument("--review-packet",required=True,type=Path)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME/"knowledge-update",read_only=True)
    draft = read(ROOT/"evaluation/knowledge/holdout-business-reference-draft.json")
    questions = read(ROOT/"evaluation/knowledge/holdout-business-questions-draft.json")
    writer = read(args.writer_directory/"question-writer-report.json")
    request = read(args.writer_directory/"request.json")
    response_bytes = (args.writer_directory/"provider-response.json").read_bytes()
    provider = json.loads(response_bytes)
    proposed = read(args.writer_directory/"candidate-questions.json")
    rejected = read(ROOT/"evidence/knowledge-update/holdout-question-writer-review.json")
    packet = read(args.review_packet)
    readiness = read(ROOT/"evidence/knowledge-update/review-readiness.json")
    cases = draft["cases"]
    checks = {"draft_fingerprint_matches": fingerprint({k:v for k,v in draft.items() if k != "content_sha256"}) == draft["content_sha256"],
        "one_completed_writer_request_bound_to_raw_output": (writer["status"] == "candidate_generation_completed" and writer["max_requests"] == 1
            and fingerprint(request) == writer["request_sha256"] and fingerprint(response_bytes) == writer["response_sha256"]
            and json.loads(provider["message"]["content"]) == proposed and fingerprint(proposed) == writer["candidate_sha256"]
            and provider["done"] is True and provider["done_reason"] == "stop" and provider["model"] == writer["model"]),
        "writer_input_is_only_task_and_literal_source_excerpts": True,
        "repetitive_writer_batch_rejected_truthfully": True,
        "thirty_distinct_draft_scenarios_and_balanced_design": True,
        "question_file_has_no_oracles_or_expected_evidence": True,
        "all_forty_development_questions_located_and_disjoint": True,
        "latest_originals_and_literal_anchor_spans_match": True,
        "answer_background_and_stop_evidence_are_separate": True,
        "semantic_independence_and_reference_freeze_remain_open": True,
        "old_development_references_and_results_preserved": True,
        "policy_registry_and_formal_approval_state_preserved": True,
        "production_review_validator_rejects_draft_files": True,
        "actual_export_contains_both_drafts_and_preserves_empty_approvals": True}
    user_data = json.loads(request["messages"][1]["content"])
    checks["writer_input_is_only_task_and_literal_source_excerpts"] = (len(request["messages"]) == 2
        and [m["role"] for m in request["messages"]] == ["system","user"] and set(user_data) == {"source_excerpts","task"}
        and request.get("think") is False and "tools" not in request and "context" not in request
        and not any(writer[key] for key in ("development_questions_in_input","reference_answers_in_input","tested_agent_outputs_in_input","implementation_in_input")))
    normalized = [question_fingerprint(q["question"]) for q in proposed["questions"]]
    checks["repetitive_writer_batch_rejected_truthfully"] = (len(normalized) == 30 and len(set(normalized)) == rejected["distinct_normalized_questions"] < 30
        and Counter(q["category"] for q in proposed["questions"]) == Counter(rejected["category_counts"])
        and rejected["category_counts"] == {"materials":30} and rejected["batch_accepted"] is False
        and rejected["model_requests_retried"] == 0 and draft["writer_batch_accepted"] is False
        and draft["draft_authorship"] == "manual_Codex_source_analysis" and draft["author"]["name"] == "Codex AI")
    checks["thirty_distinct_draft_scenarios_and_balanced_design"] = (len(cases) == len({c["id"] for c in cases}) == len({c["question_sha256"] for c in cases}) == 30
        and Counter(c["role"] for c in cases) == {"citizen":15,"staff":15}
        and Counter(c["category"] for c in cases) == dict.fromkeys(("materials","time","fee","channel","privacy","scope"),5)
        and Counter(c["proposed_outcome_after_qualified_publication"] for c in cases) == {"answer":15,"stop":15})
    expected_question_view = [{k:c[k] for k in ("id","role","question","query_date")} for c in cases]
    checks["question_file_has_no_oracles_or_expected_evidence"] = questions["cases"] == expected_question_view and questions["reference_draft_sha256"] == draft["content_sha256"] and questions["test_execution_authorized_by_this_file"] is False
    with store.db() as db:
        registry = development_registry(db)
        located = {}
        from prepare_holdout_draft import collect_questions
        for source in draft["development_source_files"]:
            path = ROOT/source["path"]
            checks["all_forty_development_questions_located_and_disjoint"] &= fingerprint(path.read_bytes()) == source["sha256"]
            collect_questions(read(path),located)
        smoke = draft["registered_smoke_query"]
        located[question_fingerprint(smoke["question"])] = smoke["question"]
        checks["all_forty_development_questions_located_and_disjoint"] &= (len(registry) == 40 and registry == draft["development_registry"]
            and fingerprint(registry) == draft["development_registry_sha256"] and set(registry) <= set(located)
            and all(question_fingerprint(c["question"]) == c["question_sha256"] and c["question_sha256"] not in registry for c in cases))
        for key,binding in draft["sources"].items():
            row,_ = store._verify(db,binding["revision"],require_review=False)
            latest = db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1",(binding["source_id"],binding["url"])).fetchone()[0]
            checks["latest_originals_and_literal_anchor_spans_match"] &= (latest == row["id"] and row["sha256"] == binding["original_sha256"]
                and row["body_sha256"] == binding["body_sha256"] and fingerprint(row["metadata"].encode()) == binding["metadata_sha256"] and binding["business_approval"] is False)
        for source,binding in zip(user_data["source_excerpts"],writer["source_bindings"],strict=True):
            store._verify(db,binding["revision"],require_review=False)
            text = db.execute("SELECT text FROM chunks WHERE revision_id=? AND locator=?",(binding["revision"],binding["locator"])).fetchone()[0]
            checks["writer_input_is_only_task_and_literal_source_excerpts"] &= (source["source_id"] == binding["source_id"] and source["locator"] == binding["locator"]
                and source["text"] == text and fingerprint(text.encode()) == binding["text_sha256"] and source["status"] == "pending_not_approved")
        for case in cases:
            for a in case["background_source_anchors"]:
                row = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?",(a["revision"],a["ordinal"])).fetchone()
                text = row["text"]
                checks["latest_originals_and_literal_anchor_spans_match"] &= (a["revision"] == draft["sources"][a["source_key"]]["revision"] and row["locator"] == a["locator"]
                    and 0 <= a["start"] < a["end"] <= len(text) and fingerprint(text.encode()) == a["parent_text_sha256"] and fingerprint(text[a["start"]:a["end"]].encode()) == a["span_sha256"])
            evidence = case["proposed_relevant_evidence"]
            expected = [] if case["proposed_outcome_after_qualified_publication"] == "stop" else [{"revision":a["revision"],"locator":a["locator"]} for a in case["background_source_anchors"]]
            checks["answer_background_and_stop_evidence_are_separate"] &= evidence == expected and (case["proposed_outcome_after_qualified_publication"] != "answer" or 1 <= len(evidence) <= 5)
            review = case["independence_review"]
            checks["semantic_independence_and_reference_freeze_remain_open"] &= (review["status"] == "draft_semantic_comparison_not_certified" and bool(review["nearest_development_topics"])
                and bool(review["decision_distinction"]) and bool(case["unresolved_prerequisites"]) and case["reference_status"] == "source_bound_draft_not_approved" and not case["tested_agent_output_used_as_reference"])
        checks["policy_registry_and_formal_approval_state_preserved"] = (fingerprint((store.root/"publication-policy.json").read_bytes()) == draft["policy_file_sha256"]
            and fingerprint(registry) == writer["development_registry_sha256"] and all(db.execute("SELECT COUNT(*) FROM "+t).fetchone()[0] == 0 for t in ("reviews","snapshot_evaluations","versions","heads")))
    checks["semantic_independence_and_reference_freeze_remain_open"] &= (draft["reference_frozen_at"] is None and draft["snapshot_binding"] is None and draft["observed_run"] is None
        and not any(draft[k] for k in ("independent_acceptance_ready","independence_certified","human_review_completed","ready_for_business_publication")) and len(draft["freeze_blockers"]) == 4)
    previous = read(ROOT/"evaluation/knowledge/material-branch-assessments.json")
    for name,expected in previous["preserved_development_files"].items():
        checks["old_development_references_and_results_preserved"] &= fingerprint((ROOT/name).read_bytes()) == expected
    for value in (draft,questions):
        try:
            validate_attributed_review(value)
        except ValueError:
            pass
        else:
            checks["production_review_validator_rejects_draft_files"] = False
    exported = packet["holdout_design_draft"]
    live = exported["live_binding_checks"]
    checks["actual_export_contains_both_drafts_and_preserves_empty_approvals"] = (
        exported["reference_draft"] == draft and exported["question_draft"] == questions
        and packet["business_questions"] == read(ROOT/"evaluation/knowledge/pilot-business-questions.json")
        and packet["ai_reference_answers"] == read(ROOT/"evaluation/knowledge/pilot-ai-reference-answers.json")
        and len(packet["records"]) == store.status()["counts"]["documents"]
        and sum(len(r["chunks"]) for r in packet["records"]) == store.status()["counts"]["latest_chunks"]
        and all(value is None for record in packet["records"] for kind in ("human_review","attributed_review") for value in record[kind].values())
        and all(live.values()) and not exported["stale_source_keys"] and exported["automatic_approval"] is False
        and packet["evaluation_contract"]["automatic_approval"] is False
        and packet["evaluation_contract"]["registered_development_question_sha256"] == registry
        and readiness["review_packet_sha256"] == fingerprint(args.review_packet.read_bytes())
        and readiness["holdout_design_draft"]["live_binding_checks"] == live
        and readiness["holdout_design_draft"]["question_count"] == 30
        and not any(readiness["holdout_design_draft"][k] for k in ("independence_certified","reference_frozen","business_acceptance_executed"))
        and readiness["business_review_completed"] is False and readiness["published_scopes"] == 0)
    report = {"captured_at":datetime.now(timezone.utc).isoformat(),"draft_integrity_passed":all(checks.values()),"checks":checks,
        "cases":30,"source_records":len(draft["sources"]),"background_anchors":sum(len(c["background_source_anchors"]) for c in cases),
        "draft_sha256":draft["content_sha256"],"writer_run":writer["run_id"],"writer_unique_questions":len(set(normalized)),
        "review_packet_sha256":fingerprint(args.review_packet.read_bytes()),
        "independence_certified":False,"business_acceptance_executed":False,"reference_frozen":False,
        "counts":store.status()["counts"],"scope":"Draft structure, exact source bindings, input separation and truthful rejection/provenance only. No semantic independence, current policy correctness or business accuracy measured."}
    (ROOT/"evidence/knowledge-update/holdout-draft-integrity.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"draft_integrity_passed":report["draft_integrity_passed"],"checks":checks,"cases":30,"writer_unique_questions":len(set(normalized)),"independence_certified":False},ensure_ascii=False))
    if not report["draft_integrity_passed"]:raise SystemExit(1)


if __name__ == "__main__":
    main()

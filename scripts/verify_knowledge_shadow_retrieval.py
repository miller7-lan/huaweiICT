"""Private, read-only development ranking of pending archives; never a release gate."""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_discovery import watches_unclassified
from gov_mcp.knowledge_retrieval import VERSION, query_grams, score_literal, top_five
from private_runtime import ensure_private_directory


ROOT = Path(__file__).resolve().parents[1]


def load_corpus(store, region, matter):
    if not store.read_only:
        raise ValueError("开发检索只接受只读连接")
    corpus, bindings = [], []
    with store.db() as db:
        db.execute("BEGIN")
        rows = db.execute("SELECT r.*,d.site_id,d.url,s.config AS source_config FROM revisions r JOIN documents d ON d.id=r.document_id "
            "JOIN sites s ON s.id=d.site_id "
            "WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY r.id").fetchall()
        for row in rows:
            meta = json.loads(row["metadata"])
            matches = meta["region"] in store.scope_regions(region) and meta["matter"] == matter
            watched = watches_unclassified(json.loads(row["source_config"]), meta, region, matter)
            if row["state"] == "excluded" or not (matches or watched):
                continue
            store._verify(db, row["id"], require_review=False)
            bindings.append({"revision_id": row["id"], "source_id": row["site_id"], "state": row["state"],
                "original_sha256": row["sha256"], "body_sha256": row["body_sha256"],
                "metadata_sha256": fingerprint(row["metadata"]), "region": meta["region"], "matter": meta["matter"],
                "development_admission": "declared_scope" if matches else "unclassified_potential_impact"})
            for chunk in db.execute("SELECT * FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],)):
                corpus.append({"revision_id": row["id"], "locator": chunk["locator"], "quote": chunk["text"],
                    "source_id": row["site_id"], "category": chunk["category"], "title": meta["title"], "url": row["url"]})
    return corpus, bindings


def rank(question, corpus):
    if not isinstance(question, str) or not question.strip() or len(question) > 300:
        raise ValueError("问题须为1至300字")
    grams = query_grams(question)
    return top_five([(score, chunk) for chunk in corpus if (score := score_literal(grams, chunk["quote"]))])


def evaluate(cases, corpus):
    available = {(chunk["revision_id"], chunk["locator"]) for chunk in corpus}
    if len(available) != len(corpus):
        raise ValueError("候选定位重复，无法作精确覆盖对照")
    rows = []
    for case in cases:
        targets = {(item["revision"], item["locator"]) for item in case["archived_evidence"]}
        if not targets <= available:
            raise ValueError("参考定位不在本次最新未排除快照；先更新或复核参考，不能静默替换")
        # Ranking sees only the question and corpus; AI answers/targets do not enter its score.
        retrieved = rank(case["question"], corpus)
        found = {(item["revision_id"], item["locator"]) for item in retrieved}
        rows.append({"case_id": case["id"], "question": case["question"],
            "ai_reference_behavior": case["expected_behavior"], "target_count": len(targets),
            "covered_targets": len(targets & found),
            "reference_locator_coverage_at5": len(targets & found)/len(targets) if targets else None,
            "max_possible_coverage_at5": min(5, len(targets))/len(targets) if targets else None,
            "target_count_exceeds_result_limit": len(targets) > 5,
            "missing_targets": [{"revision_id": r, "locator": l} for r, l in sorted(targets-found)],
            "retrieved_top5": [{k: item[k] for k in ("revision_id", "locator", "source_id", "category")}
                               | {"matches_ai_reference_locator": (item["revision_id"], item["locator"]) in targets}
                               for item in retrieved]})
    positive = [r for r in rows if r["ai_reference_behavior"] == "conditional_answer" and r["target_count"]]
    return {"records": rows, "case_count": len(rows), "conditional_reference_cases_with_targets": len(positive),
        "conditional_reference_mean_locator_coverage_at5":
            sum(r["reference_locator_coverage_at5"] for r in positive)/len(positive) if positive else None,
        "cases_with_more_than_five_reference_locators": [r["case_id"] for r in rows if r["target_count_exceeds_result_limit"]],
        "cases_without_reference_locators": [r["case_id"] for r in rows if not r["target_count"]],
        "no_reference_is_not_a_relevance_judgment": True}


def validate_reference(reference):
    cases = reference.get("cases", [])
    if (reference.get("author_kind") != "ai" or reference.get("human_review_completed") is not False
            or reference.get("ready_for_business_publication") is not False or len(cases) != 30
            or len({c["id"] for c in cases}) != 30):
        raise ValueError("需要明确AI身份且未作业务发布验收的30题开发参考")


def main():
    parser = argparse.ArgumentParser(description="只读候选检索开发对照，不调用模型或发布")
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime/knowledge-update")
    parser.add_argument("--reference", type=Path, default=ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json")
    args = parser.parse_args()
    reference_bytes = args.reference.read_bytes()
    reference = json.loads(reference_bytes)
    validate_reference(reference)
    store = KnowledgeStore(args.runtime, read_only=True)
    before = store.status()["counts"]
    policy_bytes = (store.root / "publication-policy.json").read_bytes()
    corpus, bindings = load_corpus(store, "郑州市二七区", "demo-id-renewal")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    private = store.root / "shadow-retrieval" / run_id
    ensure_private_directory(private)
    (private / "reference-frozen.json").write_bytes(reference_bytes)
    snapshot = {"bindings": bindings, "chunks": corpus}
    (private / "corpus-frozen.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    result = evaluate(reference["cases"], corpus)
    after = store.status()["counts"]
    probe = store.search("本人居民身份证到期换领需要什么材料", "郑州市二七区", "demo-id-renewal")
    report = {"run_id": run_id, "captured_at": datetime.now(timezone.utc).isoformat(),
        "kind": "pending_archive_shadow_retrieval_development", "ranker_version": VERSION,
        "reference_sha256": fingerprint(reference_bytes), "corpus_sha256": fingerprint(snapshot),
        "corpus_documents": len(bindings), "corpus_chunks": len(corpus), "bindings": bindings,
        "unclassified_potential_impact_documents": sum(b["development_admission"] == "unclassified_potential_impact" for b in bindings),
        **result, "before_counts": before, "after_counts": after,
        "counts_unchanged_during_probe": before == after,
        "publication_policy_unchanged": policy_bytes == (store.root / "publication-policy.json").read_bytes(),
        "actual_published_only_probe": {k: probe.get(k) for k in ("status", "message_code", "knowledge_version")},
        "model_requests": 0, "human_review_completed": False, "formal_retrieval_recall_measured": False,
        "business_release_ready": False, "official_publication_performed": False,
        "scope": "Same literal ranker as published search, over private verified latest unexcluded declared-scope or explicitly watched potential-impact archives. "
            "Dates, source freshness and review approval are deliberately not used to admit this development corpus. "
            "AI reference bindings are provisional locator targets, not human relevance gold; >5 targets have a coverage ceiling. "
            "No source bodies, generated policy answers or private model inputs are exported. This is not current-policy or native two-role acceptance."}
    output = ROOT / "evidence/knowledge-update"
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (output / ("shadow-retrieval-"+run_id+".json")).write_text(encoded, encoding="utf-8")
    (output / "shadow-retrieval.json").write_text(encoded, encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("run_id", "corpus_documents", "corpus_chunks",
        "conditional_reference_cases_with_targets", "conditional_reference_mean_locator_coverage_at5",
        "cases_with_more_than_five_reference_locators", "counts_unchanged_during_probe",
        "actual_published_only_probe", "business_release_ready")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Compare literal source-unit proposals on the frozen pending-archive development corpus."""

import argparse
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_classify import TOPICS
from gov_mcp.knowledge_retrieval import VERSION as RANKER_VERSION, query_grams, score_literal
from gov_mcp.knowledge_structure import VERSION as STRUCTURE_VERSION, structure
from gov_mcp.knowledge_units import VERSION, materialize, propose
from private_runtime import ensure_private_directory
from verify_knowledge_shadow_retrieval import validate_reference


ROOT = Path(__file__).resolve().parents[1]


def parent_keys(unit):
    return {(unit["revision_id"], span["locator"]) for span in unit["spans"]}


PROFILES = ("literal", "topic_priority_v1", "specific_topic_phase_v1")


def topic_codes(text):
    return {code for code, _, terms in TOPICS if any(term in text for term in terms)}


def rank_units(question, corpus, profile="literal"):
    if not isinstance(question, str) or not question.strip() or len(question) > 300:
        raise ValueError("问题须为1至300字")
    if profile not in PROFILES:
        raise ValueError("未知检索开发配置")
    grams = query_grams(question)
    topics = topic_codes(question) if profile != "literal" else set()
    topics = topics-{"eligibility"} if profile == "specific_topic_phase_v1" else topics-{"eligibility"} or topics
    collection_intent = profile == "specific_topic_phase_v1" and (
        any(term in question for term in ("代领", "邮寄", "送达", "领证", "领取新证"))
        or ("领取" in question and any(term in question for term in ("新证", "窗口")) and "申请领取" not in question))
    ranked = []
    for unit in corpus:
        priority = len(topics & topic_codes(unit["text_for_ranking"])) if topics else 0
        score = score_literal(grams, unit["text_for_ranking"])
        phase_priority = 0
        if collection_intent:
            hints = structure(unit["text_for_ranking"])
            kinds = {m["kind"] for m in hints["markers"]}
            if not hints["truncated"] and "phase:collection" in kinds:
                phase_priority = 1 if "phase:application" in kinds else 2
        if phase_priority or priority or score:
            ranked.append((phase_priority, priority, score, unit))
    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["revision_id"], item[3]["spans"][0]["locator"],
                                 item[3]["spans"][0]["range"][0], item[3]["id"]))
    selected, bindings = [], set()
    for _, _, _, unit in ranked:
        parents = parent_keys(unit)
        if not parents or len(bindings | parents) > 5:
            continue  # Never drop half a cross-page unit to fit the parent-citation budget.
        selected.append(unit)
        bindings.update(parents)
        if len(selected) == 5:
            break
    return selected


def build_units(corpus, bindings):
    grouped = defaultdict(list)
    for chunk in corpus:
        grouped[chunk["revision_id"]].append(chunk)
    units, proposals = [], []
    for binding in bindings:
        items = grouped[binding["revision_id"]]
        chunks = [{"locator": c["locator"], "category": c["category"], "text": c["quote"]} for c in items]
        if fingerprint(chunks) != binding["body_sha256"]:
            raise ValueError("冻结片段与原修订正文指纹不同")
        proposal = propose(chunks)
        if not proposal["coverage_complete"]:
            raise ValueError("单元提案不完整，不能静默缩小对照语料")
        proposals.append({"revision_id": binding["revision_id"], "source_id": binding["source_id"], "proposal": proposal})
        for unit in materialize(proposal, chunks):
            units.append({**unit, "revision_id": binding["revision_id"], "source_id": binding["source_id"]})
    return units, proposals


def evaluate_units(cases, corpus, profile="literal"):
    rows, group_suggestions = [], []
    available = set().union(*(parent_keys(u) for u in corpus)) if corpus else set()
    for case in cases:
        targets = {(e["revision"], e["locator"]) for e in case["archived_evidence"]}
        if not targets <= available:
            raise ValueError("参考定位缺失，不能缩小参考分母")
        selected = rank_units(case["question"], corpus, profile)
        parents = set().union(*(parent_keys(u) for u in selected)) if selected else set()
        rows.append({"case_id": case["id"], "question": case["question"], "ai_reference_behavior": case["expected_behavior"],
            "target_count": len(targets), "covered_parent_locators": len(targets & parents),
            "parent_locator_coverage_at5": len(targets & parents)/len(targets) if targets else None,
            "max_possible_parent_locator_coverage_at5": min(5, len(targets))/len(targets) if targets else None,
            "selected_units": [{"unit_id": u["id"], "revision_id": u["revision_id"], "source_id": u["source_id"],
                "heading_surface": u["heading_surface"], "spans": u["spans"], "flags": u["flags"],
                "text_sha256": u["text_sha256"]} for u in selected],
            "selected_parent_locator_count": len(parents),
            "missing_parent_locators": [{"revision_id": r, "locator": l} for r, l in sorted(targets-parents)],
            "unit_span_relevance_gold_available": False})
        for unit in corpus:
            refs = parent_keys(unit)
            if len(refs) > 1 and refs <= targets:
                group_suggestions.append({"case_id": case["id"], "unit_id": unit["id"], "revision_id": unit["revision_id"],
                    "parent_locators": sorted(locator for _, locator in refs), "relation": "literal_continuation_proposal",
                    "relevance_to_question": "unknown", "necessary_or_alternative": "unknown", "approved": False})
    positive = [r for r in rows if r["ai_reference_behavior"] == "conditional_answer" and r["target_count"]]
    return {"records": rows, "case_count": len(rows), "conditional_cases_with_targets": len(positive),
        "conditional_mean_parent_locator_coverage_at5": sum(r["parent_locator_coverage_at5"] for r in positive)/len(positive) if positive else None,
        "reference_group_suggestions": group_suggestions, "reference_denominators_changed": False}


def main():
    parser = argparse.ArgumentParser(description="保留原始页码/位置的检索单元开发对照，不发布")
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--profile", choices=PROFILES, default="literal")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", args.baseline_run):
        parser.error("需要已存在的精确开发运行编号")
    output = ROOT / "evidence/knowledge-update"
    baseline = json.loads((output / ("shadow-retrieval-"+args.baseline_run+".json")).read_text(encoding="utf-8"))
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    frozen = store.root / "shadow-retrieval" / args.baseline_run
    snapshot = json.loads((frozen / "corpus-frozen.json").read_text(encoding="utf-8"))
    reference_bytes = (frozen / "reference-frozen.json").read_bytes()
    if fingerprint(snapshot) != baseline["corpus_sha256"] or fingerprint(reference_bytes) != baseline["reference_sha256"]:
        raise ValueError("开发语料或参考冻结指纹不匹配")
    reference = json.loads(reference_bytes)
    validate_reference(reference)
    before = store.status()["counts"]
    policy_bytes = (store.root / "publication-policy.json").read_bytes()
    with store.db() as db:
        for binding in snapshot["bindings"]:
            row, _ = store._verify(db, binding["revision_id"], require_review=False)
            if any(row[key] != binding[key] for key in ("body_sha256",)) or row["sha256"] != binding["original_sha256"] or fingerprint(row["metadata"]) != binding["metadata_sha256"]:
                raise ValueError("原归档与冻结来源绑定不匹配")
    units, proposals = build_units(snapshot["chunks"], snapshot["bindings"])
    result = evaluate_units(reference["cases"], units, args.profile)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    private = store.root / "source-units" / run_id
    ensure_private_directory(private)
    private_value = {"proposals": proposals, "materialized_units": units}
    (private / "units-frozen.json").write_text(json.dumps(private_value, ensure_ascii=False, indent=2), encoding="utf-8")
    checks = {"all_units_reproduce_source_spans": all(p["proposal"]["coverage_complete"] for p in proposals),
        "citation_parent_budget_respected": all(r["selected_parent_locator_count"] <= 5 and len(r["selected_units"]) <= 5 for r in result["records"]),
        "counts_unchanged": before == store.status()["counts"],
        "publication_policy_unchanged": policy_bytes == (store.root / "publication-policy.json").read_bytes()}
    report = {"run_id": run_id, "baseline_run_id": args.baseline_run, "captured_at": datetime.now(timezone.utc).isoformat(),
        "kind": "literal_source_unit_development", "unit_algorithm_version": VERSION, "ranker_version": RANKER_VERSION,
        "development_rank_profile": args.profile, "topic_dictionary_sha256": fingerprint(TOPICS),
        "literal_structure_algorithm_version": STRUCTURE_VERSION,
        "topic_dictionary_modified_for_this_experiment": False,
        "corpus_sha256": baseline["corpus_sha256"], "reference_sha256": baseline["reference_sha256"],
        "private_unit_snapshot_sha256": fingerprint(private_value), "source_documents": len(proposals),
        "source_chunks": len(snapshot["chunks"]), "derived_units": len(units),
        "cross_page_units": sum(len(parent_keys(u)) > 1 for u in units),
        "baseline_conditional_mean_parent_locator_coverage_at5": baseline["conditional_reference_mean_locator_coverage_at5"],
        **result, "checks": checks, "passed_integrity_checks": all(checks.values()),
        "human_review_completed": False, "formal_retrieval_recall_measured": False, "business_release_ready": False,
        "production_query_changed": False, "official_publication_performed": False, "model_requests": 0,
        "scope": "Literal source-unit proposals on the exact earlier frozen AI development corpus. Every part remains an exact original span. "
            "Cross-page joins are proposed reading continuations, not a single contiguous source quote or approved applicability. "
            "Coverage uses unchanged coarse parent-locator targets; it does not prove that the selected span contains the relevant claim. "
            "No source-unit semantic relevance gold, independent accuracy or native business acceptance is claimed."}
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (output / ("source-unit-development-"+run_id+".json")).write_text(encoded, encoding="utf-8")
    (output / "source-unit-development.json").write_text(encoded, encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("run_id", "source_documents", "source_chunks", "derived_units", "cross_page_units",
        "conditional_mean_parent_locator_coverage_at5", "passed_integrity_checks", "business_release_ready")}, ensure_ascii=False))
    if not report["passed_integrity_checks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Run the named development scenario and evidence ablations; no live model calls."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_graph import load_pack, extract_candidates, preview_ontology, prove, SEED, file_hash

BASE = ROOT / "data/real-matter-closure/nanjing"
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
         "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu",
         "old_card_valid": True}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main():
    pack = load_pack(BASE / "review-pack-v1")
    proposals = extract_candidates(pack)
    preview = preview_ontology(proposals)
    results = {
        "before": prove(pack, FACTS),
        "after_preview_evolution": prove(pack, FACTS, ontology=preview),
        "remove_national_law": prove(pack, FACTS, ontology=preview,
            excluded_sources=("LAW-ID-BJ-TRAFFIC-2011",)),
        "remove_local_permission_sources": prove(pack, FACTS, ontology=preview,
            excluded_sources=("NJ-POLICE-FAQ-2025", "NJ-OLD-CARD-2025")),
        "keep_redundant_local_source": prove(pack, FACTS, ontology=preview,
            excluded_sources=("NJ-POLICE-FAQ-2025",)),
        "unknown_old_card_validity": prove(pack, {**FACTS, "old_card_valid": None}, ontology=preview),
        "restore_v1": prove(pack, FACTS, ontology=SEED),
    }
    assert results["before"]["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    assert results["after_preview_evolution"]["status"] == "DRAFT_PROOF_READY"
    assert all(results[k]["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF" for k in
               ("remove_national_law", "remove_local_permission_sources", "restore_v1"))
    assert results["keep_redundant_local_source"]["status"] == "DRAFT_PROOF_READY"
    assert results["unknown_old_card_validity"]["status"] == "NEEDS_CLARIFICATION"
    directory = BASE / "graph-preview-v1"
    write(directory / "seed-ontology-v1.json", SEED)
    write(directory / "candidate-relations.json", proposals)
    write(directory / "preview-ontology-v2.json", preview)
    write(directory / "development-proof-and-ablations.json", {
        "case_id": "DEV-MH01", "case_class": "public_development_case_not_holdout",
        "facts": FACTS, "results": results})
    receipt = {"stage": "02_source_relation_candidates_and_executable_preview",
        "rule_pack_sha256": file_hash((BASE / "review-pack-v1/rules-and-sources.json").read_bytes()),
        "candidate_relations": len(proposals["candidates"]), "seed_patterns": 2,
        "new_relation": "return_at", "derived_relation": "usable_until_return",
        "derivation": "Typed three-edge join of permission, phase order and same-card return obligation",
        "after_preview_proofs": len(results["after_preview_evolution"]["proofs"]),
        "development_ablation_checks": 7, "all_development_checks_passed": True,
        "model_api_calls": 0, "human_review": "pending", "knowledge_published": False,
        "independent_evaluation": False, "low_resource_efficiency_measured": False,
        "real_nexent_execution_of_this_stage": False,
        "limits": ["原文为真实来源，程序结果为未发布开发推导。",
                   "两个透明种子模板自动提出关系；不声称LLM抽取或已证明少标注效率。",
                   "恢复种子仅是预览中的本体切换，不冒充正式业务版本回滚验收。"]}
    write(ROOT / "evidence/real-matter-closure/stage-02-source-graph.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

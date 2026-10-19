"""Read-only integrity and development comparison for the downloadable input package."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_graph import load_pack, extract_candidates, preview_ontology, prove, SEED
from gov_mcp.matter_native import FACTS
from gov_mcp.matter_update import page_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=ROOT / "data/real-matter-closure/nanjing/update-snapshots-v1")
    args = parser.parse_args()
    packs = {}
    for name in ("registered-three", "observed-three", "registered-four"):
        root = args.snapshots / name; pack = load_pack(root)
        assert pack["human_review"]["status"] == "pending" and pack["knowledge_published"] is False
        for source in pack["sources"]:
            rows, metadata = page_data((root / source["original_file"]).read_bytes(), source["selector"])
            assert [(r["locator"], r["text"]) for r in rows] == [(r["locator"], r["text"]) for r in source["segments"]]
            assert metadata == source["page_observation"]
        packs[name] = pack
    before = prove(packs["registered-three"], FACTS, ontology=SEED)
    after = packs["registered-four"]
    source_only = prove(after, FACTS, ontology=SEED)
    proposals = extract_candidates(after)
    evolved = prove(after, FACTS, ontology=preview_ontology(proposals))
    assert before["status"] == source_only["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    assert evolved["status"] == "DRAFT_PROOF_READY" and all(len(p["source_ids"]) >= 2 for p in evolved["proofs"])
    conclusions = {tuple(p["triple"]) for p in evolved["proofs"]}
    assert len(conclusions) == 1
    print(json.dumps({"status": "PASSED", "mode": "offline_saved_snapshot_integrity_and_development_comparison",
        "source_counts": {k: len(p["sources"]) for k, p in packs.items()},
        "whole_body_paragraphs": {k: sum(len(s["segments"]) for s in p["sources"]) for k, p in packs.items()},
        "relation_candidates": len(proposals["candidates"]), "proof_paths": len(evolved["proofs"]),
        "distinct_conclusions": len(conclusions), "http_requests": 0, "model_calls": 0,
        "current_live_applicability_checked": False, "human_business_review": False,
        "formal_publication": False, "independent_evaluation": False}, ensure_ascii=False))


if __name__ == "__main__": main()

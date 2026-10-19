"""Proof/ablation boundaries on real-source snapshots; not independent business scoring."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from gov_mcp.matter_graph import (
    MatterProofError, SEED, canonical_hash, extract_candidates, file_hash,
    load_pack, preview_ontology, prove, safe_child,
)

PACK = Path(__file__).resolve().parents[1] / "data/real-matter-closure/nanjing/review-pack-v1"
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
    "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu",
    "old_card_valid": True}


@pytest.fixture
def pack():
    return load_pack(PACK)


def test_real_segments_generate_seed_and_new_relation_candidates(pack):
    proposals = extract_candidates(pack)
    assert proposals["manual_rule_cards_used_for_extraction"] == 0
    assert len(proposals["candidates"]) == 3
    assert {c["triple"][1] for c in proposals["candidates"]} == {"usable_during", "return_at"}
    assert all(c["review_status"] == "machine_proposed_pending_human" for c in proposals["candidates"])


def test_new_executable_relation_changes_draft_query_without_publication(pack):
    old = prove(pack, FACTS)
    ontology = preview_ontology(extract_candidates(pack))
    new = prove(pack, FACTS, ontology=ontology)
    assert old["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF"
    assert new["status"] == "DRAFT_PROOF_READY" and len(new["proofs"]) == 2
    assert all(len(p["source_ids"]) == 2 and p["proof"]["kind"] == "join" for p in new["proofs"])
    assert new["human_review"] == "pending"
    assert not new["formal_business_answer"] and not new["knowledge_published"]
    assert prove(pack, FACTS, ontology=SEED)["status"] == old["status"]


@pytest.mark.parametrize("excluded", [
    ("LAW-ID-BJ-TRAFFIC-2011",), ("NJ-POLICE-FAQ-2025", "NJ-OLD-CARD-2025"),
])
def test_removing_required_evidence_branch_stops_proof(pack, excluded):
    result = prove(pack, FACTS, ontology=preview_ontology(extract_candidates(pack)), excluded_sources=excluded)
    assert result["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF" and not result["proofs"]


def test_redundant_local_document_can_support_remaining_proof(pack):
    result = prove(pack, FACTS, ontology=preview_ontology(extract_candidates(pack)),
                   excluded_sources=("NJ-POLICE-FAQ-2025",))
    assert result["status"] == "DRAFT_PROOF_READY" and len(result["proofs"]) == 1
    assert result["proofs"][0]["source_ids"] == ["LAW-ID-BJ-TRAFFIC-2011", "NJ-OLD-CARD-2025"]


@pytest.mark.parametrize("field", list(FACTS))
def test_unknown_case_condition_does_not_default_to_main_branch(pack, field):
    facts = {**FACTS, field: None}
    assert prove(pack, facts)["status"] == "NEEDS_CLARIFICATION"


@pytest.mark.parametrize("field,value", [
    ("region", "北京市"), ("reason", "loss"), ("household", "outside_jiangsu"),
    ("civil_capacity", "limited"), ("adult", False), ("self", False),
])
def test_other_region_matter_or_applicant_branch_is_not_reused(pack, field, value):
    assert prove(pack, {**FACTS, field: value})["status"] == "OUT_OF_SCOPE"


def test_expired_old_card_cannot_get_during_processing_permission(pack):
    result = prove(pack, {**FACTS, "old_card_valid": False},
                   ontology=preview_ontology(extract_candidates(pack)))
    assert not result["proofs"]


@pytest.mark.parametrize("field,value", [("self", 1), ("adult", "true"), ("old_card_valid", 1)])
def test_case_types_are_not_coerced(pack, field, value):
    with pytest.raises(MatterProofError):
        prove(pack, {**FACTS, field: value})


def test_personal_identifiers_are_not_accepted_case_fields(pack):
    with pytest.raises(MatterProofError):
        prove(pack, {**FACTS, "id_number": "not-a-real-id"})


def test_same_document_only_cannot_be_reported_as_multidocument(pack):
    # Keep one true source's local permission but supply a forged in-memory second
    # relation as the same document. The join must not satisfy distinct sources.
    altered = deepcopy(pack)
    law = next(s for s in altered["sources"] if s["source_id"] == "LAW-ID-BJ-TRAFFIC-2011")
    law["source_id"] = "NJ-OLD-CARD-2025"
    result = prove(altered, FACTS, ontology=preview_ontology(extract_candidates(altered)),
                   excluded_sources=("NJ-POLICE-FAQ-2025",))
    assert not result["proofs"]


def test_raw_original_corruption_is_rejected(tmp_path):
    shutil.copytree(PACK, tmp_path / "pack")
    target = tmp_path / "pack/originals/NJ-OLD-CARD-2025.html"
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(MatterProofError, match="指纹"):
        load_pack(tmp_path / "pack")


def test_forged_segment_fails_even_when_manifest_is_rehashed(tmp_path):
    destination = tmp_path / "pack"
    shutil.copytree(PACK, destination)
    p = destination / "rules-and-sources.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["sources"][0]["segments"][0]["text"] += " forged"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rules_sha256"] = file_hash(p.read_bytes())
    for item in manifest["files"]:
        if item["path"] == "rules-and-sources.json":
            item["sha256"] = manifest["rules_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MatterProofError, match="原件段落"):
        load_pack(destination)


def test_source_path_escape_is_rejected(tmp_path):
    (tmp_path / "outside").write_text("outside", encoding="utf-8")
    (tmp_path / "inside").mkdir()
    with pytest.raises(MatterProofError, match="越界"):
        safe_child(tmp_path / "inside", "../outside")

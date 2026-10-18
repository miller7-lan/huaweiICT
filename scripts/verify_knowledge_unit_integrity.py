"""Verify derived-unit storage, exact spans, backup and a separately authored AI image observation."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_classify import VERSION
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_structure import VERSION as STRUCTURE_VERSION, structure
from gov_mcp.knowledge_units import VERSION as UNIT_VERSION, materialize


ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    backup = json.loads((ROOT / "evidence/knowledge-update/full-backup-restore.json").read_text(encoding="utf-8"))
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    observation = json.loads((ROOT / "evaluation/knowledge/source-unit-ai-visual-observation.json").read_text(encoding="utf-8"))
    checks = {"all_latest_revision_proposals_present": True, "proposal_hashes_match": True,
        "all_unit_spans_reproduce_source": True, "literal_markers_reproduce_source": True,
        "scope_and_obligations_not_approved": True,
        "restored_proposals_match_live": True, "prior_ai_visual_observation_bound_to_source": True,
        "cached_images_match_the_observed_bytes": True, "two_selected_unit_boundaries_match_observation": True,
        "ocr_missing_numbers_not_corrected": True, "publication_policy_lineage_matches": True}
    parents = units = spans = cross = markers = 0
    selected_source = None
    with store.db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM classification_proposals WHERE taxonomy_version=? AND revision_id IN "
            "(SELECT id FROM revisions WHERE rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id))", (VERSION,))]
        live_history = {(r["revision_id"], r["taxonomy_version"], r["sha256"]) for r in db.execute("SELECT revision_id,taxonomy_version,sha256 FROM classification_proposals")}
        latest = {r[0] for r in db.execute("SELECT id FROM revisions WHERE rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)")}
        checks["all_latest_revision_proposals_present"] = {r["revision_id"] for r in rows} == latest
        for row in rows:
            proposal = json.loads(row["suggestions"])
            checks["proposal_hashes_match"] &= fingerprint(proposal) == row["sha256"]
            raw, chunks = store._content(db, row["revision_id"])
            store._verify(db, row["revision_id"], require_review=False)
            by_locator = {c["locator"]: c["text"] for c in chunks}
            for section in proposal["sections"]:
                checks["literal_markers_reproduce_source"] &= section["source_structure"] == structure(by_locator[section["locator"]])
                markers += len(section["source_structure"]["markers"])
            p = proposal["retrieval_units"]
            values = materialize(p, chunks)  # Rejects changed proposals and checks all source coverage.
            parents += len(chunks)
            units += len(values)
            spans += sum(len(u["spans"]) for u in values)
            cross += sum(len({s["parent_ordinal"] for s in u["spans"]}) > 1 for u in values)
            checks["scope_and_obligations_not_approved"] &= (p["scope_inferred"] is False and p["human_review_completed"] is False
                and p["business_release_ready"] is False and p["material_obligations"] == p["current_effectivity"] == "unknown")
            source = db.execute("SELECT site_id FROM documents WHERE id=?", (raw["document_id"],)).fetchone()[0]
            if source == "HN-MEASURES-2021-PDF":
                selected_source = (raw, values)
    with restored.db() as db:
        saved = {(r["revision_id"], r["taxonomy_version"], r["sha256"]) for r in db.execute("SELECT revision_id,taxonomy_version,sha256 FROM classification_proposals")}
    checks["restored_proposals_match_live"] = saved == live_history
    raw, values = selected_source
    checks["prior_ai_visual_observation_bound_to_source"] = (observation["reviewer_kind"] == "ai"
        and observation["human_review_completed"] is False and observation["current_effectivity_approved"] is False
        and observation["raw_sha256"] == raw["sha256"] and observation["body_sha256"] == raw["body_sha256"]
        and observation["unit_algorithm_version"] == UNIT_VERSION)
    scans = json.loads((ROOT / "evidence/knowledge-update/henan-scan-ocr.json").read_text(encoding="utf-8"))
    render_record = next(r for r in scans["records"] if r["source_id"] == "HN-MEASURES-2021-PDF")
    observed_images = {r["name"]: r["sha256"] for r in observation["images"]}
    for name in ("page-5.png", "page-6.png"):
        actual = fingerprint((store.root / "intake-2026-10-16/henan-measures-2021-pages" / name).read_bytes())
        checks["cached_images_match_the_observed_bytes"] &= actual == observed_images[name] == render_record["rendered_page_hashes"][name] and raw["sha256"] == render_record["raw_sha256"]
    chosen = [next(u for u in values if u["heading_surface"] and word in u["heading_surface"]) for word in ("省内通办", "压缩身份证")]
    checks["two_selected_unit_boundaries_match_observation"] = ([u["id"] for u in chosen] == observation["unit_ids"]
        and [p["locator"] for p in chosen[0]["parts"]] == ["PDF第5页（OCR待核对）"]
        and [p["locator"] for p in chosen[1]["parts"]] == ["PDF第5页（OCR待核对）", "PDF第6页（OCR待核对）"]
        and all(word not in u["text_for_ranking"] for word in ("特殊群体", "旅馆", "交通违法", "上门受理") for u in chosen)
        and all(word in chosen[1]["text_for_ranking"] for word in ("40 日", "30 日")))
    checks["ocr_missing_numbers_not_corrected"] = all(u["heading_surface"].startswith("、") and "ocr_heading_number_missing_not_corrected" in u["flags"] for u in chosen)
    plan = json.loads((ROOT / "evidence/knowledge-update/model-prevalidation-plan.json").read_text(encoding="utf-8"))
    policy_sha = fingerprint(publication_policy(store))
    policy_unchanged = policy_sha == plan["publication_policy_sha256"]
    transition = ROOT / "evidence/knowledge-update/review-contract-upgrade.json"
    change = json.loads(transition.read_text(encoding="utf-8")) if transition.exists() else {}
    checks["publication_policy_lineage_matches"] = policy_unchanged or (
        change.get("passed") is True and change.get("previous_policy_sha256") == plan["publication_policy_sha256"]
        and change.get("policy_sha256") == policy_sha and change.get("quality_thresholds_unchanged") is True)
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "taxonomy_version": VERSION,
        "documents": len(rows), "canonical_parent_chunks": parents, "derived_units": units, "source_spans": spans,
        "literal_structure_version": STRUCTURE_VERSION, "literal_markers": markers,
        "classification_history_records_compared": len(live_history),
        "cross_page_proposals": cross, "counts": store.status()["counts"], "checks": checks, "passed": all(checks.values()),
        "visual_observation_sha256": fingerprint(observation), "human_review_completed": False,
        "publication_policy_sha256": policy_sha, "policy_unchanged_since_model_plan": policy_unchanged,
        "policy_transition_audit_sha256": fingerprint(transition.read_bytes()) if not policy_unchanged and transition.exists() else None,
        "scope": "Source/proposal/backup integrity across latest archives and binding to the separately recorded AI observation of two scanned units only. No other visual boundary, current policy, material relationship or business acceptance is approved."}
    (ROOT / "evidence/knowledge-update/source-unit-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "documents": len(rows), "units": units, "checks": checks}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

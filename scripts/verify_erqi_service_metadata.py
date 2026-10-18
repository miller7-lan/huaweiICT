"""Verify the captured Erqi time explanations and preserve the reviewed comparison sources."""
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME
from verify_erqi_material_capture import dialog_body, jpeg_dimensions


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    private = store.root / "source-research/erqi-service-metadata-20261018"
    before = read(private / "before-main.json")
    report = read(ROOT / "evaluation/knowledge/erqi-service-metadata-capture-20261018.json")
    expected = dict(before["counts"])
    for key, value in {"revisions": 1, "chunks": 6, "latest_chunks": 3}.items():
        expected[key] += value
    if store.status()["counts"] != expected:
        raise SystemExit("主库已有后续更新或数量差异；保留时限取证阶段回执，先核对当前状态")
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    with store.db() as db:
        row, _ = store._verify(db, report["revision"], require_review=False)
        _, chunks = store._content(db, row["id"])
        prior, _ = store._verify(db, report["prior_revision"], require_review=False)
        _, old_chunks = store._content(db, prior["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (report["source_id"],)).fetchone()[0])
        metadata = json.loads(row["metadata"])
        configs = {r["id"]: fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")}
        old_revisions = {r["id"]: fingerprint(dict(r)) for r in db.execute("SELECT * FROM revisions WHERE id<>?", (row["id"],))}
        gates = {t: [fingerprint(dict(r)) for r in db.execute("SELECT * FROM " + t)] for t in before["gates"]}
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
        comparison = True
        comparison_texts = []
        for binding in report["existing_reviewed_comparison_bindings"]:
            original, _ = store._verify(db, binding["revision"])
            _, parts = store._content(db, original["id"])
            selected = next(c for c in parts if c["locator"] == binding["locator"])
            doc = db.execute("SELECT site_id,url FROM documents WHERE id=?", (original["document_id"],)).fetchone()
            current = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (original["document_id"],)).fetchone()[0]
            comparison &= current == binding["revision"] and doc["site_id"] == binding["source_id"] and doc["url"] == binding["url"] and original["sha256"] == binding["original_sha256"] and fingerprint(selected["text"].encode()) == binding["text_sha256"]
            comparison_texts.append(selected["text"])
    raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
    prior_raw = (store.root / "originals" / prior["sha256"][:2] / prior["sha256"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("capture-manifest.json"))
        assets = {name: archive.read(name) for name in archive.namelist() if name != "capture-manifest.json"}
        unique_members = len(archive.namelist()) == len(assets) + 1 == 10
    with zipfile.ZipFile(io.BytesIO(prior_raw)) as archive:
        prior_assets = {name: archive.read(name) for name in archive.namelist() if name != "capture-manifest.json"}
    expanded = assets["expanded-basic-ax.txt"].decode("utf-8")
    start = expanded.index("heading 编码信息, Value: 3")
    code_section = expanded[start:expanded.index("161 link Description: 收起", start)]
    codes = re.findall(r"^\s+\d+ text (.+)$", code_section, re.M)
    expected_texts = ["\n".join(codes), dialog_body(assets["commitment-explanation-ax.txt"], "承诺办理时限"), dialog_body(assets["statutory-explanation-ax.txt"], "法定办理时限")]
    catalog_entry = next(r for r in read(ROOT / "data/knowledge/candidate-catalog.json")["documents"] if r["source_id"] == report["source_id"])
    checks = {
        "report_and_private_receipt_match": fingerprint({k: v for k, v in report.items() if k != "content_sha256"}) == report["content_sha256"] and {k: v for k, v in report.items() if k != "content_sha256"} == read(private / "archive-receipt.json"),
        "latest_capture_bundle_and_all_assets_bound": latest == row["id"] and raw == (private / "capture-bundle.zip").read_bytes() and fingerprint(raw) == report["capture_archive_sha256"] == row["sha256"] and len(raw) == report["capture_archive_bytes"] and unique_members and manifest == report["manifest"] == metadata["source_observation"]["capture_manifest"] and manifest["file_inventory"] == {name: {"bytes": len(value), "sha256": fingerprint(value)} for name, value in assets.items()},
        "previous_assets_and_all_three_material_chunks_preserved": all(assets[name] == value for name, value in prior_assets.items()) and chunks[:3] == old_chunks and len(old_chunks) == 3,
        "three_new_complete_literal_selections_match": [c["text"] for c in chunks[3:]] == expected_texts and report["new_selected_clauses"] == [{"locator": c["locator"], "text_sha256": fingerprint(c["text"].encode()), "characters": len(c["text"])} for c in chunks[3:]],
        "specific_item_identity_codes_bound": codes[0] == "编码信息" and len(codes) == 9 and dict(zip(codes[1::2], codes[2::2])) == report["visible_codes"] == metadata["source_observation"]["service_metadata"]["visible_codes"] and report["visible_codes"]["业务办理项编码"] == "11410100MB1K20802F400070911000201",
        "time_labels_exclusions_and_statute_reference_kept_separate": "text 60个工作日" in expanded and "text 6个工作日" in expanded and "补件、上报（转报）" in expected_texts[1] and expected_texts[2] == "《中华人民共和国居民身份证法》第十二条" and report["visible_time_labels"] == {"commitment_workdays": 6, "statutory_workdays": 60},
        "new_dialogs_bound_to_same_region_authority_and_source": all(report["url"] in assets[name].decode("utf-8") and "郑州市二七区" in assets[name].decode("utf-8") and "郑州市公安局二七分局" in assets[name].decode("utf-8") for name in ["expanded-basic-ax.txt", "commitment-explanation-ax.txt", "statutory-explanation-ax.txt"]),
        "observed_screenshot_retained_as_jpeg": jpeg_dimensions(assets["commitment-explanation.jpg"]) == (1280, 720) and assets["commitment-explanation.jpg"] == (private / "commitment-explanation.jpg").read_bytes(),
        "reviewed_law_and_provincial_end_to_end_measure_remain_current_bindings": comparison and len(comparison_texts) == 2 and "之日起六十日内" in comparison_texts[0] and "从受理之日起到领证之日" in comparison_texts[1] and "15个工作日" in comparison_texts[1],
        "capture_is_pending_with_conflicts_dates_and_coverage_unapproved": row["state"] == "pending_review" and metadata["scope_confirmed"] is False and metadata["conflict"] is True and all(metadata[k] is None for k in ["published_at", "effective_from", "effective_to"]) and metadata["source_observation"]["service_metadata"]["page_update_date_observed"] is False and all(v is False for k, v in report["interpretation"].items() if k != "commitment_excludes_generic_extra_steps"),
        "no_new_source_or_automatic_collection_or_http_original_claim": set(configs) == set(before["configs"]) and all(configs[key] == value for key, value in before["configs"].items() if key != report["source_id"]) and config["enabled"] is False and config["access_review"]["automatic_fetch_approved"] is False and metadata["archive_format"] == "agent_created_zip_capture_bundle_not_government_original" and report["normal_http_guide_originals_added"] == 0,
        "all_old_revisions_and_review_publication_rows_unchanged": old_revisions == before["revisions"] and gates == before["gates"],
        "draft_scope_and_publication_policy_bytes_unchanged": all(fingerprint((ROOT / path).read_bytes()) == sha for path, sha in before["file_sha256"].items()),
        "catalog_exports_correct_latest_capture_identity": catalog_entry["revision"] == row["id"] and catalog_entry["metadata"]["acquisition_kind"] == "public_browser_accessibility_capture" and catalog_entry["state"] == "pending_review",
    }
    with restored.db() as db:
        copied, _ = restored._verify(db, row["id"], require_review=False)
        _, copied_chunks = restored._content(db, row["id"])
    checks["full_restore_preserves_latest_package_metadata_and_six_chunks"] = backup["passed"] is True and copied["sha256"] == row["sha256"] and copied["metadata"] == row["metadata"] and copied["state"] == "pending_review" and copied_chunks == chunks and len(chunks) == 6
    try:
        store.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        checks["unreviewed_content_still_blocks_formal_candidate"] = str(error) == "Unreviewed or changed content"
    else:
        checks["unreviewed_content_still_blocks_formal_candidate"] = False
    result = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks, "counts": store.status()["counts"], "new_selected_clauses": 3, "new_sources": 0, "normal_http_guide_originals_added": 0, "business_approvals": 0, "business_publications": 0, "model_requests": 0, "limits": "Capture identity, exact observed text and preservation; no six-day end-to-end or postal guarantee, currentness certification, policy override or business acceptance."}
    (ROOT / "evidence/knowledge-update/erqi-service-metadata-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

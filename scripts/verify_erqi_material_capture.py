"""Check a manual browser capture without relabelling it as a government HTTP original."""
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import AccessStopped, Fetcher
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dialog_body(raw, title):
    section = raw.decode("utf-8").rsplit("container " + title + "\n", 1)[1].split("The focused UI element", 1)[0]
    texts = re.findall(r"^\t\t\d+ text (.+)$", section, re.M)
    if len(texts) != 1:
        raise ValueError("One complete dialog body required")
    return texts[0]


def jpeg_dimensions(raw):
    if raw[:2] != b"\xff\xd8":
        raise ValueError("JPEG source bytes required")
    index = 2
    while index < len(raw):
        if raw[index] != 255:
            raise ValueError("Invalid JPEG marker")
        while raw[index] == 255:
            index += 1
        marker = raw[index]
        index += 1
        if marker in {0xD8, 0xD9, *range(0xD0, 0xD8)}:
            continue
        length = int.from_bytes(raw[index:index + 2], "big")
        if length < 2 or index + length > len(raw):
            raise ValueError("Invalid JPEG segment")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            return (int.from_bytes(raw[index + 5:index + 7], "big"), int.from_bytes(raw[index + 3:index + 5], "big"))
        index += length
    raise ValueError("JPEG dimensions missing")


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    private = store.root / "source-research/erqi-material-popovers-20261018"
    report = read(ROOT / "evaluation/knowledge/erqi-material-dialog-capture-20261018.json")
    receipt, before = read(private / "archive-receipt.json"), read(private / "before-main.json")
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    previous = KnowledgeStore(read(private / "before-backup.json")["directory"], read_only=True)
    expected_counts = dict(before["counts"])
    for key, increment in {"sites": 1, "documents": 1, "revisions": 2, "chunks": 6, "latest_chunks": 3}.items():
        expected_counts[key] += increment
    if store.status()["counts"] != expected_counts:
        raise SystemExit("主库已有后续更新或非预期差异；保留本阶段回执，先检查当前状态")
    with store.db() as db:
        row, _ = store._verify(db, report["revision"], require_review=False)
        _, chunks = store._content(db, row["id"])
        config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (report["source_id"],)).fetchone()[0])
        metadata = json.loads(row["metadata"])
        raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
        configs = {r["id"]: fingerprint(r["config"].encode()) for r in db.execute("SELECT id,config FROM sites")}
        old_revisions = {r["id"]: fingerprint(dict(r)) for r in db.execute("SELECT * FROM revisions WHERE id NOT IN (?,?)", (row["id"], report["initial_revision"]))}
        initial, _ = store._verify(db, report["initial_revision"], require_review=False)
        _, initial_chunks = store._content(db, initial["id"])
        gates = {t: [fingerprint(dict(r)) for r in db.execute("SELECT * FROM " + t)] for t in before["gates"]}
    with previous.db() as db:
        previous_gates = {t: [fingerprint(dict(r)) for r in db.execute("SELECT * FROM " + t)] for t in before["gates"]}
        old_proposals = {(r["revision_id"], r["taxonomy_version"]): fingerprint(dict(r)) for r in db.execute("SELECT * FROM classification_proposals")}
    with store.db() as db:
        proposals = {(r["revision_id"], r["taxonomy_version"]): fingerprint(dict(r)) for r in db.execute("SELECT * FROM classification_proposals") if r["revision_id"] not in {row["id"], report["initial_revision"]}}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        expected_members = {"capture-manifest.json", "initial-guide-dom.txt", "material-notes-ax.txt", "material-acceptance-ax.txt", "material-basis-ax.txt", "material-basis.jpg"}
        if len(archive.namelist()) != len(expected_members) or set(archive.namelist()) != expected_members:
            raise ValueError("Unexpected browser capture bundle members")
        manifest = json.loads(archive.read("capture-manifest.json"))
        assets = {name: archive.read(name) for name in expected_members - {"capture-manifest.json"}}
    expected_dialogs = [dialog_body(assets[name], title) for name, title in [
        ("material-notes-ax.txt", "填报须知"), ("material-acceptance-ax.txt", "查看受理标准"), ("material-basis-ax.txt", "材料依据")]]
    catalog = read(ROOT / "data/knowledge/candidate-catalog.json")
    entry = next(r for r in catalog["documents"] if r["source_id"] == report["source_id"])
    first_observation = read(ROOT / "evaluation/knowledge/erqi-rendered-guide-observation-20261018.json")
    checks = {
        "public_report_receipt_and_digest_match": {k: v for k, v in report.items() if k != "content_sha256"} == receipt and report["content_sha256"] == fingerprint(receipt),
        "private_bundle_matches_content_addressed_archive": (private / "capture-bundle.zip").read_bytes() == raw and fingerprint(raw) == report["capture_archive_sha256"] == row["sha256"] and len(raw) == report["capture_archive_bytes"],
        "all_five_capture_assets_and_manifest_bound": manifest == report["manifest"] == metadata["source_observation"]["capture_manifest"] and manifest["file_inventory"] == {name: {"bytes": len(value), "sha256": fingerprint(value)} for name, value in assets.items()} and all((private / name).read_bytes() == assets[name] for name in assets if name != "initial-guide-dom.txt"),
        "initial_guide_observation_retained_and_separate": fingerprint(assets["initial-guide-dom.txt"]) == first_observation["rendered_capture"]["sha256"] and first_observation["conclusions"]["candidate_ingested"] is False and first_observation["source"]["guide_original_sha256"] is None,
        "same_region_authority_and_exact_source_url_in_all_dialog_captures": all(report["url"] in assets[name].decode("utf-8") and "郑州市二七区" in assets[name].decode("utf-8") and "郑州市公安局二七分局" in assets[name].decode("utf-8") for name in ["material-notes-ax.txt", "material-acceptance-ax.txt", "material-basis-ax.txt"]) and manifest["source_url"] == report["url"] == config["seeds"][0],
        "three_complete_dialog_bodies_reproduce_archived_chunks": [c["text"] for c in chunks] == expected_dialogs and report["selected_dialogs"] == [{"locator": c["locator"], "category": c["category"], "characters": len(c["text"]), "text_sha256": fingerprint(c["text"].encode())} for c in chunks],
        "original_card_submission_and_collection_stages_not_merged": expected_dialogs[:2] == ["换领身份证需要提供原身份证", "内容真实"] and "《中华人民共和国居民身份证法》第十一条" in expected_dialogs[2] and "领取新证时，必须交回原证" in expected_dialogs[2] and "居民户口簿" not in "".join(expected_dialogs),
        "basis_screenshot_actual_jpeg_format_and_dimensions_retained": manifest["screenshot_media_type"] == "image/jpeg" and jpeg_dimensions(assets["material-basis.jpg"]) == (1280, 720),
        "format_correction_preserves_first_archive_image_and_literal_history": initial["sha256"] == report["initial_capture_archive_sha256"] and (private / "initial-capture-bundle.zip").read_bytes() == (store.root / "originals" / initial["sha256"][:2] / initial["sha256"]).read_bytes() and initial_chunks == chunks and assets["material-basis.jpg"] == (private / "material-basis.png").read_bytes() and metadata["source_observation"]["format_correction_only"] is True,
        "capture_package_never_claims_government_download_or_http_guide_original": report["archive_format"] == metadata["archive_format"] == "agent_created_zip_capture_bundle_not_government_original" and report["acquisition_kind"] == metadata["acquisition_kind"] == "public_browser_accessibility_capture" and manifest["guide_download_obtained"] is False and manifest["normal_http_guide_body_obtained"] is False and report["normal_http_guide_originals_added"] == 0,
        "pending_scope_conflict_and_unknown_dates_preserved": row["state"] == "pending_review" and metadata["scope_confirmed"] is False and metadata["conflict"] is True and all(metadata[k] is None for k in ["published_at", "effective_from", "effective_to"]) and metadata["source_fact_assessment"]["business_approval"] is False and metadata["source_fact_assessment"]["human_review_completed"] is False,
        "all_old_sources_revisions_classification_and_gates_preserved": {k: v for k, v in configs.items() if k != report["source_id"]} == before["configs"] and old_revisions == before["revisions"] and gates == previous_gates and proposals == old_proposals,
        "protected_drafts_scope_and_publication_policy_unchanged": all(fingerprint((ROOT / name).read_bytes()) == sha for name, sha in before["file_sha256"].items()),
        "catalog_exposes_capture_type_and_no_business_approval": entry["revision"] == row["id"] and entry["metadata"]["acquisition_kind"] == metadata["acquisition_kind"] and entry["metadata"]["archive_format"] == metadata["archive_format"] and entry["source_fact_assessment_summary"]["business_approval"] is False,
    }
    try:
        Fetcher(restored).fetch(config, report["url"])
    except AccessStopped as error:
        checks["disabled_capture_source_stops_before_http"] = config["enabled"] is False and config["access_review"]["automatic_fetch_approved"] is False and str(error) == "Source disabled" and error.phase == "preflight"
    else:
        checks["disabled_capture_source_stops_before_http"] = False
    try:
        restored.review(row["id"], "Codex AI isolated rejection check", confirmed=True)
    except ValueError as error:
        checks["actual_review_validator_rejects_unconfirmed_scope"] = str(error) == "Known region/matter and confirmed branch scope required before review"
    else:
        checks["actual_review_validator_rejects_unconfirmed_scope"] = False
    with restored.db() as db:
        copied, _ = restored._verify(db, row["id"], require_review=False)
        _, copied_chunks = restored._content(db, row["id"])
    checks["complete_restore_preserves_capture_metadata_chunks_and_state"] = backup["passed"] is True and copied["sha256"] == row["sha256"] and copied["metadata"] == row["metadata"] and copied["state"] == row["state"] and copied_chunks == chunks and (restored.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes() == raw
    try:
        store.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        checks["formal_candidate_still_rejects_unreviewed_content"] = str(error) == "Unreviewed or changed content"
    else:
        checks["formal_candidate_still_rejects_unreviewed_content"] = False
    result = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks, "counts": store.status()["counts"], "new_browser_capture_records": 1, "selected_dialogs": 3, "normal_http_guide_originals_added": 0, "business_approvals": 0, "business_publications": 0, "model_requests": 0, "limits": "Capture integrity, exact observed wording, existing-state preservation and gate rejection only; not government-file authenticity, legal currentness or complete local material approval."}
    (ROOT / "evidence/knowledge-update/erqi-material-capture-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

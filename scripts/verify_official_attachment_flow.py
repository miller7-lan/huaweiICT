"""Actual unseen-URL discovery using real official pages in a new isolated store.

These are historically published files first seen by this isolated database,
not evidence that a new policy was issued today. No synthetic business facts,
human attestations, services or formal publications are created.
"""

import copy
import argparse
import json
import re
import uuid
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_backup import create_bundle, restore_bundle
from gov_mcp.knowledge_fetch import schedule, worker_once
from nexent_api import ROOT, RUNTIME
from prepare_henan_attachments import PARENT, collector_configs


URLS = ["https://hnga.henan.gov.cn/2025/10-21/3237046.html", "https://hnga.henan.gov.cn/2025/10-21/3237047.html"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="Verify an already completed isolated store; no repeated network calls")
    args = parser.parse_args()
    if args.resume and not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", args.resume):
        raise ValueError("Exact completed proof identifier required")
    real = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    original_counts = (json.loads((ROOT / "evidence/knowledge-update/intake-step-status.json").read_text(encoding="utf-8"))["counts"]
                       if args.resume else real.status()["counts"])
    with real.db() as db:
        parent = copy.deepcopy(json.loads(db.execute("SELECT config FROM sites WHERE id=?", (PARENT,)).fetchone()[0]))
    stamp = args.resume or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    path = RUNTIME / "knowledge-attachment-proofs" / stamp
    store = KnowledgeStore(path) if args.resume else KnowledgeStore.initialize(path)
    parent.update(seeds=URLS, allowed_paths=["/2025/10-21/3237046.html", "/2025/10-21/3237047.html"], page_kind="document", discover_links=False)
    for key in list(parent):
        if key.startswith("discovery_") or key == "index_urls":
            parent.pop(key)
    parent["attachment_discovery"] = {"sources": [c["id"] for c in collector_configs(parent)], "owners": {}, "max_links": 5}
    parent["access_review"]["purpose"] = "isolated_actual_official_attachment_discovery_verification"
    if args.resume:
        if any(job["state"] != "done" for job in store.status()["jobs"]):
            raise ValueError("Resume verifies terminal completed jobs only; inspect existing live handles instead of restarting")
    else:
        for config in collector_configs(parent):
            store.register(config)
        store.register(parent)
        schedule(store)
        for _ in range(12):
            if not worker_once(store, owner="official-attachment-proof"):
                break
    with store.db() as db:
        db.execute("UPDATE workers SET progress='completed' WHERE id='official-attachment-proof'")
        records = []
        for row in db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id ORDER BY r.rowid"):
            store._verify(db, row["id"], require_review=False)
            metadata = json.loads(row["metadata"])
            extraction = metadata.get("source_observation", {}).get("pdf_extraction", {})
            records.append({"source_id": row["site_id"], "url": row["url"], "revision": row["id"], "raw_sha256": row["sha256"],
                "state": row["state"], "region": metadata["region"], "matter": metadata["matter"], "effective_from": metadata["effective_from"],
                "extraction_method": extraction.get("method"), "ocr_pages": len(extraction.get("raw_ocr_pages", [])),
                "parent_signal": metadata.get("source_observation", {}).get("discovery_signal")})
    status = store.status()
    pdfs = [r for r in records if r["extraction_method"] == "windows_media_ocr"]
    checks = {"two_actual_html_parents": len(records)-len(pdfs) == 2,
        "two_pdfs_automatically_discovered_and_acquired": len(pdfs) == 2 and len(status["attachments"]) == 2,
        "eighteen_actual_ocr_pages": sum(r["ocr_pages"] for r in pdfs) == 18,
        "all_normal_jobs_finished": all(j["state"] == "done" for j in status["jobs"]),
        "all_candidates_stay_unclassified_pending": all(r["state"] == "pending_review" and r["region"] == "unknown" and r["matter"] == "unclassified" and r["effective_from"] is None for r in records),
        "no_formal_publication_or_services": status["counts"]["heads"] == status["counts"]["versions"] == 0,
        "main_runtime_counts_unchanged": real.status()["counts"] == original_counts}
    if all(checks.values()):
        backup = create_bundle(store, store.root / "backups" / "proof-check")
        restored = restore_bundle(backup["directory"], store.root.parent / (stamp+"-restored"))
        reader = KnowledgeStore(restored["restored_to"], read_only=True)
        checks["attachment_graph_and_candidates_restore"] = reader.status()["attachments"] == status["attachments"] and reader.status()["pending_review"] == status["pending_review"]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "records": records, "counts": status["counts"], "attachments": status["attachments"], "jobs": status["jobs"],
        "main_runtime_counts": original_counts, "human_attestations_created": 0, "services_started": False,
        "resumed_completed_store_without_network": bool(args.resume),
        "scope": "Actual official HTML-to-PDF discovery, normal guarded acquisition, native OCR and complete graph restore in a new isolated database. These historic documents are newly discovered by that database, not newly issued policies or approved business answers."}
    (ROOT / "evidence/knowledge-update/official-attachment-flow.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "counts": status["counts"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

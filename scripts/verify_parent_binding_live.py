"""Queue one allowed completed parent job, then observe the real worker result.

The verifier never downloads a PDF itself or marks a source check successful.
There is no retry loop; queued/running observations keep the same job handle.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import allowed_url, validate_config
from nexent_api import ROOT, RUNTIME


def pdf_state(db, targets):
    return {"revisions": [list(r) for r in db.execute("SELECT d.site_id,r.id,r.state FROM revisions r JOIN documents d ON d.id=r.document_id ORDER BY r.rowid") if r[0] in targets],
            "jobs": [list(r) for r in db.execute("SELECT site_id,url,state,attempts,error FROM jobs ORDER BY id") if r[0] in targets],
            "resources": [list(r) for r in db.execute("SELECT site_id,url,checked_at FROM resources ORDER BY site_id,url") if r[0] in targets],
            "success": [list(r) for r in db.execute("SELECT id,last_success FROM sites ORDER BY id") if r[0] in targets]}


def main():
    parser = argparse.ArgumentParser(description="一次已准入父网页的真实后台复查；不自行重试或取附件")
    parser.add_argument("--review-directory", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enqueue", action="store_true")
    mode.add_argument("--observe", action="store_true")
    args = parser.parse_args()
    research = args.review_directory.resolve()
    if not research.is_relative_to((RUNTIME / "knowledge-update/source-research").resolve()):
        raise ValueError("Dedicated private review directory required")
    baseline_path = research / "live-parent-check.json"
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=not args.enqueue)
    if args.enqueue:
        if baseline_path.exists():
            raise ValueError("A check was already queued; observe that job rather than enqueue again")
        plan = json.loads((research / "review-plan.json").read_text(encoding="utf-8"))
        parent_revision = plan["expected_revisions"]["rev-80b25a42abaf5893115d827d"]
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            parent, _ = store._verify(db, parent_revision, require_review=False)
            document = db.execute("SELECT * FROM documents WHERE id=?", (parent["document_id"],)).fetchone()
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (document["id"],)).fetchone()[0]
            source = db.execute("SELECT * FROM sites WHERE id=?", (document["site_id"],)).fetchone()
            config = json.loads(source["config"])
            validate_config(config); allowed_url(config, document["url"])
            if latest != parent_revision or not config["enabled"] or source["paused_reason"] or source["failures"]:
                raise ValueError("The reviewed parent or its existing access state changed")
            if db.execute("SELECT 1 FROM jobs WHERE site_id=? AND state IN ('queued','running')", (source["id"],)).fetchone():
                raise ValueError("Observe the existing acquisition job before requesting this check")
            job = db.execute("SELECT * FROM jobs WHERE site_id=? AND url=?", (source["id"], document["url"])).fetchone()
            if not job or job["state"] != "done":
                raise ValueError("Only the already completed exact parent job can be rechecked")
            targets = plan["offline_replay"]["target_sources"]
            stamp = time.time()
            baseline = {"queued_at": stamp, "job_id": job["id"], "source_id": source["id"], "url": document["url"],
                "parent_revision": parent_revision, "parent_document": document["id"], "original_sha256": parent["sha256"],
                "pdf_targets": targets, "pdf_state": pdf_state(db, targets), "counts": store.status()["counts"],
                "policy_sha256": fingerprint((store.root / "publication-policy.json").read_bytes())}
            baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
            db.execute("UPDATE jobs SET state='queued',attempts=0,due=?,lease_until=NULL,error=NULL WHERE id=?", (stamp, job["id"]))
            store.log(db, "post_review_parent_check_queued", {"job_id": job["id"], "site": source["id"], "url": document["url"], "parent_revision": parent_revision})
        print(json.dumps({"job_id": job["id"], "state": "queued", "owner_expected": "knowledge-worker", "manual_retries": 0}))
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    with store.db() as db:
        job = dict(db.execute("SELECT id,state,attempts,owner,error FROM jobs WHERE id=?", (baseline["job_id"],)).fetchone())
        if job["state"] in ("queued", "running"):
            print(json.dumps({"terminal": False, "job": job}, ensure_ascii=False)); return
        parent = db.execute("SELECT * FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (baseline["parent_document"],)).fetchone()
        resource = db.execute("SELECT checked_at FROM resources WHERE site_id=? AND url=?", (baseline["source_id"], baseline["url"])).fetchone()
        success = [json.loads(r[0]) for r in db.execute("SELECT data FROM audit WHERE action='check_succeeded' AND at>=? AND json_extract(data,'$.site')=? ORDER BY id", (baseline["queued_at"], baseline["source_id"]))]
        checks = {"real_background_parent_check_completed": job["state"] == "done" and job["error"] is None and job["owner"] == "knowledge-worker"
                  and bool(success) and bool(resource) and resource[0] >= baseline["queued_at"],
                  "same_reviewed_parent_original_and_disposition_retained": parent["id"] == baseline["parent_revision"] and parent["sha256"] == baseline["original_sha256"] and parent["state"] == "excluded",
                  "no_extra_pdf_fetch_job_or_revision": pdf_state(db, baseline["pdf_targets"]) == baseline["pdf_state"],
                  "no_new_document_or_published_version": all(store.status()["counts"][k] == baseline["counts"][k] for k in ("documents", "revisions", "chunks", "latest_chunks", "versions", "heads")),
                  "business_policy_unchanged": fingerprint((store.root / "publication-policy.json").read_bytes()) == baseline["policy_sha256"]}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
              "job": job, "parent_url": baseline["url"], "parent_revision": baseline["parent_revision"],
              "normal_worker_status_records": success, "pdf_targets": baseline["pdf_targets"], "counts": store.status()["counts"],
              "scope": "One permitted exact parent recheck through the real worker and ordinary transport. No PDF replay was labelled as a fresh HTTP check, no policy approval or long-term acceptance."}
    (ROOT / "evidence/knowledge-update/parent-binding-live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"terminal": True, "passed": report["passed"], "checks": checks, "job": job}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

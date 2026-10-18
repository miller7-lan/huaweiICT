"""Bounded AI scope review of the observed metadata-only refresh, with offline replay.

No automatic future exclusion rule is installed. These ten inspected revisions
are individually pinned; changed bytes/body/fields require another review.
"""

import argparse
import copy
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_attachments import discover_attachments
from gov_mcp.knowledge_backup import create_bundle, restore_bundle
from gov_mcp.knowledge_impact import revision_impact
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


REASONS = {
    "rev-060d2e3a458b69be7cd82fa2": "保安登记、旅馆许可及治安系统通知，不提供本试点到期换领条款",
    "rev-0a2cd4b66a6c6c1e5a0d6538": "2019年清理通知的已核原件入口，不能据此判定2022规范在2026年的效力",
    "rev-477237f4f11bb3b1e9180233": "2022年三件废止文件涉及娱乐场所、自行车和网安裁量，未列试点规范",
    "rev-b4e27a0baa07b04b2033aa5d": "五件废止文件涉及特行、民爆、交通等，不是本试点身份证条款",
    "rev-571462a796240be516bd509c": "十项措施涉及新生儿、记录证明、许可、外国人及交通等，未提供试点到期换领规则",
    "rev-542383b9685ff607d29b6ea2": "HTML只作已归档2021废止通知PDF入口，不充当重复正文；PDF范围判断保留",
    "rev-80b25a42abaf5893115d827d": "HTML只作2021便民措施PDF入口，正文另在待核PDF；不能推定该PDF当前全部适用",
    "rev-b816053b4ba69f8b83ebf63e": "HTML只作已归档2020废止通知PDF入口，不充当额外政策正文",
    "rev-6938de360787a1cf06a2b03e": "2020废止通知PDF仍未列试点规范；原OCR日期错误不修写，不反推其他文件全部有效",
    "rev-455ffba059929543e9bd3fe7": "2021废止通知PDF仍未列试点规范，文件日期与网页日期分开保留，未新增视觉核查声明",
}
ALLOWED_SCOPE_FIELDS = {"_excluded_after_revision", "selected_content_scope", "source_observation.fields.meta:lastmodifiedtime",
                        "source_observation.discovery_signal.hints.parent_revision"}
NORM = "rev-dfede4448f8bccedf15a59ec"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def state(store):
    with store.db() as db:
        return {"latest": [list(r) for r in db.execute("SELECT document_id,id,state FROM revisions WHERE rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY document_id")],
            "configs": fingerprint([list(r) for r in db.execute("SELECT id,config,paused_reason FROM sites ORDER BY id")]),
            "policy": fingerprint((store.root / "publication-policy.json").read_bytes()),
            "formal": {t: db.execute("SELECT COUNT(*) FROM "+t).fetchone()[0] for t in ("reviews", "snapshot_evaluations", "versions", "heads")}}


def apply_plan(store, plan, expected=None):
    results = {}
    for case in plan["cases"]:
        with store.db() as db:
            row, _ = store._verify(db, case["revision"], require_review=False)
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
            if expected and latest == expected[case["revision"]]:
                store._verify_exclusion(db, latest)
                decision = db.execute("SELECT reason FROM exclusions WHERE revision_id=?", (latest,)).fetchone()[0]
                if decision != case["reason"]:
                    raise ValueError("A different review decision already exists")
                results[case["revision"]] = latest
                continue
            if latest != row["id"] or row["sha256"] != case["original_sha256"] or row["body_sha256"] != case["body_sha256"]:
                raise ValueError("A pinned candidate changed during review")
        result = store.exclude(case["revision"], "Codex AI observation refresh review", case["reason"], confirmed=True)
        store.propose_classification(result)
        results[case["revision"]] = result
    norm = plan["norm"]
    with store.db() as db:
        row, _ = store._verify(db, norm["revision"], require_review=False)
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
    if expected and latest == expected[NORM]:
        with store.db() as db:
            current, _ = store._verify(db, latest, require_review=False)
            if json.loads(current["metadata"]) != norm["metadata"] or current["state"] != "pending_review":
                raise ValueError("The interpreted-date candidate was modified or approved")
        result = latest
    elif latest == NORM:
        result = store.amend(NORM, norm["metadata"])
        store.propose_classification(result)
    else:
        raise ValueError("The dated normative source changed during review")
    results[NORM] = result
    if expected and results != expected:
        raise ValueError("Actual review revisions differ from the inspected offline results")
    return results


def replay(store, plan, results):
    parents = [case for case in plan["cases"] if case["site_id"] == "HN-POLICY-DISCOVERY"]
    with store.db() as db:
        targets = {r[0] for case in parents for r in db.execute("SELECT target_site_id FROM attachment_edges WHERE parent_document_id=? AND target_site_id IS NOT NULL", (case["document_id"],))}
    def observation():
        with store.db() as db:
            return {"pdf_revisions": [list(r) for r in db.execute("SELECT d.site_id,r.id,r.state,r.metadata FROM revisions r JOIN documents d ON d.id=r.document_id ORDER BY r.rowid") if r[0] in targets],
                    "pdf_jobs": [list(r) for r in db.execute("SELECT site_id,url,state,attempts,error FROM jobs ORDER BY id") if r[0] in targets],
                    "success": [list(r) for r in db.execute("SELECT id,last_success FROM sites ORDER BY id") if r[0] in targets]}
    before = observation()
    with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS during archived replay")) as dns, \
         patch("socket.create_connection", side_effect=AssertionError("No network during archived replay")) as network:
        for _ in range(2):
            for case in parents:
                with store.db() as db:
                    config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (case["site_id"],)).fetchone()[0])
                    row, _ = store._verify(db, results[case["revision"]], require_review=False)
                raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
                discover_attachments(store, config, row["id"], raw, time.time())
        external_calls = dns.call_count + network.call_count
    with store.db() as db:
        edges_current = all(all(r[0] == results[case["revision"]] for r in db.execute("SELECT parent_revision FROM attachment_edges WHERE parent_document_id=?", (case["document_id"],))) for case in parents)
    return {"rounds": 2, "html_parents": len(parents), "target_sources": sorted(targets), "external_calls": external_calls,
            "pdf_revisions_jobs_and_success_unchanged": before == observation(), "edges_keep_actual_review_revision": edges_current}


def prepare(store, research):
    stamp = datetime.now(timezone.utc).isoformat()
    plan = {"schema": 1, "run_id": "observation-refresh-20261016-"+uuid.uuid4().hex[:8], "prepared_at": stamp,
        "actor": {"kind": "ai", "name": "Codex AI"}, "human_review_completed": False,
        "before": state(store), "counts_before": store.status()["counts"], "cases": []}
    if any(plan["before"]["formal"].values()):
        raise ValueError("Inspect any formal review/publication before this bounded candidate review")
    with store.db() as db:
        if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
            raise ValueError("Wait for the active acquisition job before capturing this plan")
        for revision, summary in REASONS.items():
            row, _ = store._verify(db, revision, require_review=False)
            previous = db.execute("SELECT * FROM revisions WHERE document_id=? AND rowid<(SELECT rowid FROM revisions WHERE id=?) ORDER BY rowid DESC LIMIT 1", (row["document_id"], revision)).fetchone()
            store._verify_exclusion(db, previous["id"])
            prior_reason = db.execute("SELECT reason FROM exclusions WHERE revision_id=?", (previous["id"],)).fetchone()[0]
            document = db.execute("SELECT * FROM documents WHERE id=?", (row["document_id"],)).fetchone()
            impact = revision_impact(store, revision)
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
            if (row["state"] != "pending_review" or row["sha256"] != previous["sha256"] or row["body_sha256"] != previous["body_sha256"]
                    or not set(impact["changed_metadata_fields"]) <= ALLOWED_SCOPE_FIELDS
                    or latest != revision):
                raise ValueError("Candidate is outside the individually inspected metadata-only cohort")
            reason = summary+"。本次原件/正文指纹与旧排除修订"+previous["id"]+"相同，仅复核已列观察字段及内部提示；继续排除本试点答复，不判定整份文件现行效力。"
            plan["cases"].append({"revision": revision, "document_id": row["document_id"], "site_id": document["site_id"], "url": document["url"],
                "original_sha256": row["sha256"], "body_sha256": row["body_sha256"], "previous_exclusion": previous["id"],
                "previous_reason_sha256": fingerprint(prior_reason.encode()), "changed_metadata": impact["changed_metadata_fields"], "reason": reason})
        norm, _ = store._verify(db, NORM, require_review=False)
        old, _ = store._verify(db, "rev-30d1bf98f40e9acb897b37c1", require_review=False)
        if norm["sha256"] != old["sha256"] or norm["body_sha256"] != old["body_sha256"]:
            raise ValueError("The historical-date evidence changed")
        metadata, old_metadata = json.loads(norm["metadata"]), json.loads(old["metadata"])
        assessment = copy.deepcopy(old_metadata["source_fact_assessment"])
        body = {r["locator"]: r["text"] for r in db.execute("SELECT locator,text FROM chunks WHERE revision_id=?", (NORM,))}
        for fact in assessment["facts"]:
            evidence = fact["evidence"]
            if "locator" in evidence:
                if body[evidence["locator"]][evidence["start"]:evidence["end"]] != evidence["quote"]:
                    raise ValueError("A literal historical source fact no longer matches")
            else:
                if evidence["quote"] not in metadata["source_observation"]["fields"]["block:page_date"][0]:
                    raise ValueError("The observed historical page date differs")
                evidence["observation_sha256"] = fingerprint(metadata["source_observation"])
            evidence["revision"] = NORM
        if old_metadata["effective_from"] != "2022-09-01" or old_metadata["published_at"] != "2022-08-16":
            raise ValueError("Previously interpreted dates differ from this review")
        assessment.update(id="source-facts-observation-refresh-20261016-v2", assessed_at=stamp, base_revision=NORM,
                          revalidated_from_revision=old["id"], base_body_sha256=norm["body_sha256"])
        metadata.update(effective_from="2022-09-01", published_at="2022-08-16", source_fact_assessment=assessment,
                        business_review="pending_current_applicability")
        plan["norm"] = {"revision": NORM, "original_sha256": norm["sha256"], "body_sha256": norm["body_sha256"], "metadata": metadata,
            "basis": "Same original and body; all historical literal facts and current page-date observation rechecked. Source observations unchanged. Not current legal-effect approval."}
    preserved = read(ROOT / "evaluation/knowledge/material-branch-assessments.json")["preserved_development_files"]
    plan["preserved_development_files"] = preserved
    plan["question_hashes"] = [c["question_sha256"] for c in read(ROOT / "evaluation/knowledge/holdout-business-reference-draft.json")["cases"]]
    backup_parent = store.root / "backups"
    restore_parent = RUNTIME / "knowledge-update-restores"
    ensure_private_directory(backup_parent)
    ensure_private_directory(restore_parent)
    backup = create_bundle(store, backup_parent / (plan["run_id"]+"-pre-review"))
    restored = restore_bundle(backup["directory"], restore_parent / (plan["run_id"]+"-offline-review"))
    clone = KnowledgeStore(restored["restored_to"])
    results = apply_plan(clone, plan)
    trial = replay(clone, plan, results)
    if not trial["pdf_revisions_jobs_and_success_unchanged"] or not trial["edges_keep_actual_review_revision"] or trial["external_calls"]:
        raise ValueError("Offline replay did not preserve PDF state and actual edge provenance")
    plan.update(expected_revisions=results, offline_replay=trial, backup=backup, restore=restored)
    plan["plan_sha256"] = fingerprint(plan)
    (research / "review-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": True, "cases": len(plan["cases"]), "norm_dates_revalidated_not_approved": True, "offline_replay": trial}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="十份指定资料的AI范围复核及历史日期重核；先离线复放，再显式应用")
    parser.add_argument("--research-directory", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    research = args.research_directory.resolve()
    if not research.is_relative_to((RUNTIME / "knowledge-update/source-research").resolve()):
        raise ValueError("Dedicated private source-review directory required")
    ensure_private_directory(research)
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    plan_path = research / "review-plan.json"
    if args.prepare:
        if plan_path.exists():
            raise ValueError("Review plan already exists; inspect rather than repeat preparation")
        prepare(store, research)
        return
    plan = read(plan_path)
    if fingerprint({k:v for k,v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("Prepared review plan changed")
    if state(store) != plan["before"]:
        raise ValueError("Canonical candidate/source/policy state changed since offline review")
    with store.db() as db:
        if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
            raise ValueError("Do not apply during a live acquisition job")
    results = apply_plan(store, plan, plan["expected_revisions"])
    trial = replay(store, plan, results)
    after = state(store)
    checks = {"ten_pinned_scope_decisions_applied": len(results) == 11 and results == plan["expected_revisions"],
        "real_archived_replay_keeps_pdf_versions_jobs_and_success": trial["pdf_revisions_jobs_and_success_unchanged"],
        "actual_parent_dispositions_retained_in_edges": trial["edges_keep_actual_review_revision"],
        "no_network_in_archived_replay": trial["external_calls"] == 0,
        "source_configs_pause_and_business_policy_unchanged": after["configs"] == plan["before"]["configs"] and after["policy"] == plan["before"]["policy"],
        "no_formal_approval_evaluation_or_publication": not any(after["formal"].values()),
        "old_frozen_development_files_preserved": all(fingerprint((ROOT/path).read_bytes()) == expected for path,expected in plan["preserved_development_files"].items()),
        "working_questions_not_used_or_changed": plan["question_hashes"] == [c["question_sha256"] for c in read(ROOT / "evaluation/knowledge/holdout-business-reference-draft.json")["cases"]]}
    report = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "actor": plan["actor"], "human_review_completed": False, "plan_sha256": plan["plan_sha256"], "cases": plan["cases"],
        "result_revisions": results, "historical_dates_revalidated_not_current_effect_approved": True,
        "offline_replay": plan["offline_replay"], "actual_archived_replay": trial, "counts_before": plan["counts_before"], "counts": store.status()["counts"],
        "business_approval_performed": False, "scope": "Individually pinned same-original scope exclusions and literal historical-date interpretation; no automatic future exclusions or current-policy approval."}
    (ROOT / "evaluation/knowledge/metadata-refresh-scope-decisions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "counts": report["counts"], "archive_replay": trial}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

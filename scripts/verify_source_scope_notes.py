"""Audit actual review-only source notes without changing the corpus or reference drafts."""
import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_notes import bind_scope_notes
from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-packet", required=True, type=Path)
    args = parser.parse_args()
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    before = read(store.root / "source-research/online-scope-notes-20261018/before-main.json")
    if fingerprint(store.path.read_bytes()) != before["database_sha256"]:
        raise SystemExit("主库已有后续变化；保留本阶段只读注释回执，先核对当前来源绑定")
    artifact = read(ROOT / "evaluation/knowledge/source-scope-notes.json")
    scope_sha = fingerprint((ROOT / "config/knowledge-scope.json").read_bytes())
    bound = bind_scope_notes(store, artifact, scope_sha)
    note = artifact["notes"][0]
    packet = read(args.review_packet)
    backup = read(ROOT / "evidence/knowledge-update/full-backup-restore.json")
    restored = KnowledgeStore(backup["restore"]["restored_to"], read_only=True)
    bindings_after_restore = bind_scope_notes(restored, artifact, scope_sha)
    clauses, metadata = {}, {}
    with store.db() as db:
        for key, binding in note["sources"].items():
            row, _ = store._verify(db, binding["revision"], require_review=binding["state"] == "reviewed")
            _, chunks = store._content(db, row["id"])
            clauses[key] = {c["locator"]: c["text"] for c in chunks}
            metadata[key] = json.loads(row["metadata"])
        gates = {t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in ["reviews", "snapshot_evaluations", "versions", "heads"]}
    d24, d25 = clauses["D24"]["正文段落5"], clauses["D25"]["正文段落3"]
    dates = {}
    for key, meta in metadata.items():
        fields = meta["source_observation"]["fields"]
        dates[key] = {"page_date": fields.get("block:page_date", []), "meta_pubdate": fields.get("meta:pubdate", []), "meta_lastmodifiedtime": fields.get("meta:lastmodifiedtime", []), "effective_from": meta["effective_from"], "date_meaning": "Observed source-page fields only; not legal commencement or proof of current local eligibility."}
    changed = deepcopy(artifact)
    changed["notes"][0]["sources"]["D25"]["metadata_sha256"] = "changed synthetic binding"
    changed["content_sha256"] = fingerprint({k: v for k, v in changed.items() if k != "content_sha256"})
    rejected = bind_scope_notes(store, changed, scope_sha)
    checks = {
        "actual_note_bindings_and_all_literal_anchors_valid": bound["status"] == "bound" and len(bound["notes"]) == 1 and len(note["sources"]) == 3 and len(note["anchors"]) == 12 and len(note["facts"]) == 9,
        "actual_export_contains_exact_review_only_note": packet["source_scope_notes"] == bound and packet["source_scope_notes"]["business_approval"] is False,
        "2024_city_age_recency_and_lost_damage_conditions_retained": all(text in d24 for text in ["郑州市、焦作市", "本市户籍", "16岁以上", "丢失、损坏", "未超2年"]) and "当地群众补领换领" in clauses["D24"]["正文段落6"],
        "2025_age_fingerprints_recency_and_cause_retained": all(text in d25 for text in ["未超5年", "申领时年满16周岁", "指纹采集正常", "丢失或损坏", "刷脸实人认证"]) and "本省户籍" in clauses["D25"]["正文段落1"],
        "fee_and_pickup_branches_not_detached_from_the_source": all(text in d25 for text in ["居民身份证丢失补领、损坏换领工本费均为40元", "受理机关领取、邮政快递领取"]),
        "historical_and_new_duration_units_and_exam_conditions_are_distinct": "25个自然日" in clauses["D24"]["正文段落1"] and "15个自然日" in clauses["D24"]["正文段落3"] and all(text in clauses["D26"]["正文段落1"] for text in ["从受理之日起到领证之日", "15个工作日", "5个工作日", "中考、高考特殊时期"]),
        "date_observations_reproduce_metadata_without_new_effective_dates": note["date_observations"] == dates and metadata["D25"]["source_observation"]["fields"]["block:page_date"] == ["2025-05-05"] and metadata["D25"]["effective_from"] is None,
        "all_three_binding_states_and_empty_review_forms_preserved": all(next(r for r in packet["records"] if r["revision"] == binding["revision"])["state"] == binding["state"] for binding in note["sources"].values()) and all(r["human_review"]["decision"] is None and r["attributed_review"]["decision"] is None for r in packet["records"]),
        "reference_and_policy_bytes_unchanged": all(fingerprint((ROOT / path).read_bytes()) == sha for path, sha in before["file_sha256"].items()),
        "database_bytes_counts_and_formal_gates_unchanged": fingerprint(store.path.read_bytes()) == before["database_sha256"] and store.status()["counts"] == before["counts"] and gates == {"reviews": 9, "snapshot_evaluations": 0, "versions": 0, "heads": 0},
        "existing_full_restore_also_binds_the_same_repository_note": backup["passed"] is True and bindings_after_restore == bound,
        "changed_binding_withholds_analysis_from_the_review_package": rejected["status"] == "stale" and rejected["notes"] == [] and rejected["withheld"] == [{"id": note["id"], "reason": "source_binding_changed"}],
        "unpublished_visitor_query_still_stops": store.search("身份证到期换领", "郑州市二七区", "demo-id-renewal")["message_code"] == "NO_PUBLISHED_KNOWLEDGE",
    }
    result = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks, "counts": store.status()["counts"], "source_scope_notes_sha256": fingerprint((ROOT / "evaluation/knowledge/source-scope-notes.json").read_bytes()), "review_only_facts": 9, "source_bindings": 3, "literal_anchors": 12, "new_model_requests": 0, "new_business_approvals": 0, "new_business_publications": 0, "limits": "AI source-scope analysis and read-only export binding, not legal currentness, expiry-online eligibility, oracle correctness or independent business acceptance."}
    (ROOT / "evidence/knowledge-update/source-scope-notes-integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

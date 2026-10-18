"""Verify legal-text identity, dated directory limits and real candidate-only intake."""

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import parse_html, select_articles
from gov_mcp.knowledge_impact import revision_impact
from gov_mcp.knowledge_review import development_registry
from nexent_api import ROOT, RUNTIME
from prepare_national_law_lineage import BASE_LAW, SPECS


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def main():
    parser = argparse.ArgumentParser(description="核对版本身份与草案绑定，不批准现行效力或业务发布")
    parser.add_argument("--research-directory", required=True, type=Path)
    args = parser.parse_args()
    research = args.research_directory.resolve()
    before = read(research / "before-canonical-intake.json")
    records = read(research / "intake-records.json")["records"]
    prior_draft = read(research / "holdout-before-refresh.json")
    draft = read(ROOT / "evaluation/knowledge/holdout-business-reference-draft.json")
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    checks = {key: True for key in ("two_pinned_normal_originals_match", "both_nonoperational_sources_are_excluded",
        "dated_directory_entry_and_date_types_match", "historical_validity_flag_not_treated_as_version_proof",
        "all_twenty_three_articles_reproduce_existing_original", "four_old_clauses_retained_and_content_equivalent",
        "old_original_and_source_pause_unchanged", "directory_real_job_completed_and_daily_monitor_ready",
        "old_frozen_development_artifacts_unchanged", "working_holdout_questions_unchanged_and_latest_sources_rebound",
        "business_policy_registry_and_zero_approval_state_preserved")}
    findings = []
    with store.db() as db:
        latest = {}
        for key, record in records.items():
            row, _ = store._verify(db, record["revision"], require_review=False)
            expected = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
            if expected != row["id"]:
                raise ValueError("Inspected legal-source revision is no longer latest")
            latest[key] = row
        for key, spec in SPECS.items():
            row = latest[key]
            raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
            receipt = read(research / (spec["key"]+"-receipt.json"))
            checks["two_pinned_normal_originals_match"] &= (row["sha256"] == spec["sha256"] == fingerprint(raw)
                and raw == (research / spec["file"]).read_bytes() and receipt["status"] == 200 and receipt["final_url"] == spec["url"])
            store._verify_exclusion(db, row["id"])
            checks["both_nonoperational_sources_are_excluded"] &= row["state"] == "excluded"
        directory = latest["NATIONAL-LAW-DIRECTORY-2026"]
        historical = latest["NATIONAL-LAW-HISTORICAL-2003"]
        expanded = latest["EXISTING-ID-LAW-2011"]
        def chunks(row):
            return [dict(c) for c in db.execute("SELECT ordinal,locator,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (row["id"],))]
        dc, hc, nc = chunks(directory), chunks(historical), chunks(expanded)
        dm, hm, nm = (json.loads(r["metadata"]) for r in (directory, historical, expanded))
        fields = dm["source_observation"]["fields"]
        checks["dated_directory_entry_and_date_types_match"] = (dc[0]["text"] == "39．中华人民共和国居民身份证法（2003年通过，2011年修正）"
            and "截至2026年8月28日" in fields["block:page_title"][0] and "2026-09-18 16:33" in fields["block:page_date_and_source"][0]
            and fields["meta:pubdate"] == ["2026-09-18 16:35"] and dm["effective_from"] is None and dm["effective_to"] is None)
        old = db.execute("SELECT * FROM revisions WHERE id=?", (BASE_LAW,)).fetchone()
        old_chunks = chunks(old)
        raw_law = (store.root / "originals" / old["sha256"][:2] / old["sha256"]).read_bytes()
        parsed, _ = parse_html(raw_law, {"class": "content-txt"}, min_text_length=1)
        reextracted = select_articles(parsed, [c["locator"] for c in nc])
        checks["all_twenty_three_articles_reproduce_existing_original"] = (len(nc) == len(reextracted) == 23
            and all(a["locator"] == b["locator"] and a["text"] == b["text"] for a, b in zip(nc, reextracted, strict=True))
            and nm["scope_confirmed"] is False and expanded["state"] == "pending_review")
        new_by_locator = {c["locator"]: c for c in nc}
        checks["four_old_clauses_retained_and_content_equivalent"] = (len(old_chunks) == 4 and old["body_sha256"] == before["law"]["body_sha256"]
            and all(compact(c["text"]) == compact(new_by_locator[c["locator"]]["text"]) for c in old_chunks))
        historical_by_locator = {c["locator"]: c for c in hc}
        checks["historical_validity_flag_not_treated_as_version_proof"] = (hm["source_observation"]["fields"]["block:reported_validity"] == ["[有效性] 是"]
            and "指纹" not in historical_by_locator["第三条"]["text"] and "指纹信息" in new_by_locator["第三条"]["text"]
            and "人民警察有下列行为" in historical_by_locator["第十九条"]["text"]
            and "金融、电信、交通、教育、医疗" in new_by_locator["第十九条"]["text"] and not hm["scope_confirmed"])
        source = db.execute("SELECT * FROM sites WHERE id='ID-LAW-01'").fetchone()
        checks["old_original_and_source_pause_unchanged"] = (old["sha256"] == expanded["sha256"] == before["law"]["original_sha256"]
            and fingerprint(source["config"].encode()) == before["old_law_source_config_sha256"]
            and source["paused_reason"] == before["old_law_paused_reason"] and not json.loads(source["config"])["enabled"])
        monitor = db.execute("SELECT * FROM sites WHERE id='NATIONAL-LAW-DIRECTORY-2026'").fetchone()
        config = json.loads(monitor["config"])
        jobs = [dict(r) for r in db.execute("SELECT id,state,owner,error FROM jobs WHERE site_id=?", (monitor["id"],))]
        checks["directory_real_job_completed_and_daily_monitor_ready"] = (bool(jobs) and all(j["state"] == "done" and j["error"] is None for j in jobs)
            and config["enabled"] and config["interval_seconds"] == 86400 and monitor["last_success"] is not None and monitor["paused_reason"] is None
            and config["seeds"] == [SPECS[monitor["id"]]["url"]] and config["paragraph_expected_count"] == 1)
        background = []
        for row in db.execute("SELECT r.*,d.site_id FROM revisions r JOIN documents d ON d.id=r.document_id "
                "WHERE r.created_at>=1792159820 AND r.created_at<1792160050 ORDER BY r.rowid"):
            previous = db.execute("SELECT * FROM revisions WHERE document_id=? AND rowid<(SELECT rowid FROM revisions WHERE id=?) ORDER BY rowid DESC LIMIT 1", (row["document_id"], row["id"])).fetchone()
            impact = revision_impact(store, row["id"])
            job = db.execute("SELECT j.id,j.state,j.owner FROM jobs j JOIN documents d ON d.site_id=j.site_id AND d.url=j.url WHERE d.id=?", (row["document_id"],)).fetchone()
            background.append({"site_id": row["site_id"], "revision": row["id"], "previous_revision": previous["id"], "previous_state": previous["state"],
                "same_original": row["sha256"] == previous["sha256"], "same_body": row["body_sha256"] == previous["body_sha256"],
                "changed_metadata": impact["changed_metadata_fields"], "current_state": row["state"], "job_receipt": dict(job) if job else None})
        checks["business_policy_registry_and_zero_approval_state_preserved"] = (fingerprint((store.root / "publication-policy.json").read_bytes()) == before["policy_sha256"]
            and len(development_registry(db)) == 40 and all(db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0] == 0 for table in ("reviews", "snapshot_evaluations", "versions", "heads")))
        def anchor(row, chunk):
            return {"revision": row["id"], "ordinal": chunk["ordinal"], "locator": chunk["locator"], "start": 0, "end": len(chunk["text"]),
                "parent_text_sha256": fingerprint(chunk["text"].encode()), "span_sha256": fingerprint(chunk["text"].encode())}
        findings = [
            {"id": "dated_edition_identity", "finding": "目录记录截至2026-08-28的2011修正版本身份；网页显示2026-09-18 16:33，meta标16:35，两者不作法律施行日。", "anchors": [anchor(directory, dc[0])]},
            {"id": "validity_badge_is_not_text_edition", "finding": "历史2003文本页面有效性原标记为是，网页显示为现行有效；其第三条缺少2011文本指纹内容，不能单凭页面标签选择法律版本。", "anchors": [anchor(historical, historical_by_locator["第三条"]), anchor(expanded, new_by_locator["第三条"])]},
            {"id": "article_numbers_need_version_binding", "finding": "2003与2011的第十九条指向不同正文，引用必须绑定版本、原件和具体条文，不能只按同名法律及条号拼接。", "anchors": [anchor(historical, historical_by_locator["第十九条"]), anchor(expanded, new_by_locator["第十九条"])]},
            {"id": "original_act_and_amendment_dates_differ", "finding": "2003第22条与2011第23条均提原法2004施行；2011末条另有2012/2013证件过渡内容。不能把原法起始日直接改作2011修改决定起始日，修改决定正常原件链仍未补齐。", "anchors": [anchor(historical, historical_by_locator["第二十二条"]), anchor(expanded, new_by_locator["第二十三条"])]},
            {"id": "same_archived_original_more_complete_extraction", "finding": "既有原件从4条重提取为23条，原件与原四条保留；不是重新抓取暂停来源，也不是已核定当前全部条款。", "anchors": [anchor(expanded, new_by_locator["第十一条"])]}]
    for path, expected in before["preserved_development_files"].items():
        checks["old_frozen_development_artifacts_unchanged"] &= fingerprint((ROOT / path).read_bytes()) == expected
    checks["working_holdout_questions_unchanged_and_latest_sources_rebound"] = (before["old_holdout_content_sha256"] == prior_draft["content_sha256"]
        and before["holdout_questions"] == [c["question_sha256"] for c in draft["cases"]]
        and draft["sources"]["LAW"]["revision"] == expanded["id"] and not draft["independence_certified"] and draft["reference_frozen_at"] is None)
    receipts = [read(path) for path in sorted(research.glob("*-receipt.json"))]
    assessment = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "reviewer_kind": "ai", "reviewer": "Codex AI",
        "human_review_completed": False, "current_applicability_after_2026_08_28": "not_certified", "business_release_ready": False,
        "sources": records, "findings": findings, "acquisition_observations": receipts, "background_refresh_observations": background,
        "draft_binding_refresh": {"previous_content_sha256": prior_draft["content_sha256"], "content_sha256": draft["content_sha256"],
            "question_hashes_unchanged": before["holdout_questions"] == [c["question_sha256"] for c in draft["cases"]]},
        "limits": ["目录只覆盖其截止日期，不认证此后现行效力；精确页监测不保证发现另一个URL的新目录。", "SAMR栏目入口为客户端框架，未配置新目录自动发现；未模拟签名或调用模板构建接口。", "暂停来源没有恢复，TLS/证书失败未关闭校验；404和采集失败均不推断法律废止。", "后台另有12份相同原件的观察字段/父修订变化，其中10份曾排除的资料重新待核，未自动沿用排除。", "本地材料、六个月调整、收费关系和独立业务验收仍未解决。"]}
    assessment["assessment_sha256"] = fingerprint(assessment)
    (ROOT / "evaluation/knowledge/national-law-lineage-assessments.json").write_text(json.dumps(assessment, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    report = {"captured_at": assessment["captured_at"], "passed": all(checks.values()), "checks": checks,
        "new_originals": 2, "existing_law_articles": 23, "source_records": len(records), "findings": len(findings),
        "source_anchors": sum(len(f["anchors"]) for f in findings), "assessment_sha256": assessment["assessment_sha256"],
        "background_refresh_count": len(background), "directory_jobs": jobs, "counts": store.status()["counts"],
        "business_release_ready": False, "scope": "Original/edition identity, extraction, historical exclusion, bounded monitoring and reference separation; not a current-policy or business-accuracy approval."}
    (ROOT / "evidence/knowledge-update/national-law-lineage-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "counts": report["counts"], "background_refresh_count": len(background)}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

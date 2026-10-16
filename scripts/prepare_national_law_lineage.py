"""Retain two normally acquired legal references and re-extract an existing original.

No fresh request is made to the paused Xinmi source. Historical text and a dated
valid-law directory are excluded from operational answers, not declared revoked.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import allowed_url, observe_html, parse_html, schedule, select_articles, validate_config, worker_once
from nexent_api import ROOT, RUNTIME


BASE_LAW = "rev-d66f8c0116dc5bea8410ab62"
SPECS = {
    "NATIONAL-LAW-DIRECTORY-2026": {"key": "samr-directory", "file": "samr-effective-directory-2026.html",
        "url": "https://www.samr.gov.cn/fgs/sjdt/art/2026/art_93a4fbac2ce14ab6baab60b0cd452ef4.html",
        "sha256": "ad8056246fd1a2641b73c21e611ae7f222b0d0788bb2ef2201ae3905e2f37caa",
        "label": "市场监管总局转载：截至2026-08-28现行法律目录的身份证法条目",
        "reason": "法律目录仅用作截至2026-08-28的版本身份旁证，不是二七换领办理条款或2026-10-16效力证明。排除业务答案；继续每日检查这个精确页面，不能代表已发现以后另一个URL的新目录。"},
    "NATIONAL-LAW-HISTORICAL-2003": {"key": "beijing-2003", "file": "beijing-id-law-2003.html",
        "url": "https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/200401/t20040101_781229.html",
        "sha256": "d3a7c952b80f3251df8744a63152985823342bc0428a2beb9b084f1864c683d8",
        "label": "北京政府历史对照：2003身份证法第三、十一、十九、二十二条",
        "reason": "正文为2003年22条版本，网页有效性标记不能替代2011修正关系核对。保留历史对照并排除当前业务答案；未宣告整部法律或整站失效。本页不启用监测。"},
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="正常原件核对及既有2011全文重新提取；不核发业务批准")
    parser.add_argument("--research-directory", required=True, type=Path)
    args = parser.parse_args()
    research = args.research_directory.resolve()
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    baseline_path = research / "before-canonical-intake.json"
    if baseline_path.exists():
        raise ValueError("This bounded intake has already started; inspect its state instead of repeating")
    stamp = datetime.now(timezone.utc).isoformat()
    with store.db() as db:
        if db.execute("SELECT COUNT(*) FROM heads").fetchone()[0]:
            raise ValueError("Inspect any live publication before this candidate-only re-extraction")
        row, _ = store._verify(db, BASE_LAW, require_review=False)
        latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
        if latest != BASE_LAW:
            raise ValueError("The previously inspected national-law revision is no longer latest")
        original = store.root / "originals" / row["sha256"][:2] / row["sha256"]
        raw_law, old_metadata = original.read_bytes(), json.loads(row["metadata"])
        document = dict(db.execute("SELECT * FROM documents WHERE id=?", (row["document_id"],)).fetchone())
        previous_law = {"revision": BASE_LAW, "original_sha256": row["sha256"], "body_sha256": row["body_sha256"]}
        old_source = db.execute("SELECT config,paused_reason FROM sites WHERE id=?", (document["site_id"],)).fetchone()
        if json.loads(old_source["config"])["enabled"] or not old_source["paused_reason"]:
            raise ValueError("Inspect changed national-law access state before offline re-extraction")
        old_source_hash = fingerprint(old_source["config"].encode())
    full, _ = parse_html(raw_law, {"class": "content-txt"}, min_text_length=1)
    articles = select_articles(full, [f"第{n}条" for n in ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十", "二十一", "二十二", "二十三")])
    if len(articles) != 23 or "指纹信息" not in articles[2]["text"] or "国家决定换发新一代" not in articles[10]["text"]:
        raise ValueError("The existing original is not the inspected complete 2011 text")
    prepared = {}
    for identifier, spec in SPECS.items():
        raw = (research / spec["file"]).read_bytes()
        receipt = read(research / (spec["key"]+"-receipt.json"))
        if (receipt.get("status") != 200 or receipt["url"] != spec["url"] or receipt["final_url"] != spec["url"]
                or fingerprint(raw) != spec["sha256"] or receipt["sha256"] != spec["sha256"]):
            raise ValueError("Pinned normal Fetcher receipt or original changed")
        with KnowledgeStore(research / "probe", read_only=True).db() as db:
            probe = json.loads(db.execute("SELECT config FROM sites WHERE id=?", ("NPC-"+spec["key"].upper()+"-PROBE",)).fetchone()[0])
        validate_config(probe); allowed_url(probe, spec["url"])
        directory = identifier == "NATIONAL-LAW-DIRECTORY-2026"
        metadata = {"title": spec["label"], "region": "unknown", "matter": "unclassified", "scope_confirmed": False,
            "document_type": "dated_legal_status_directory" if directory else "historical_law_text",
            "collection_role": "legal_status_signal" if directory else "governance_reference",
            "effective_from": None, "effective_to": None, "published_at": None, "business_review": "not_operational_answer_evidence",
            "human_review_completed": False, "usage_basis": probe["metadata"]["usage_basis"], "selected_content_scope": spec["reason"]}
        config = dict(probe, id=identifier, label=spec["label"], enabled=directory, metadata=metadata,
                      content_selector={"class": "zt_xilan_07"} if directory else {"id": "mainText"})
        config["access_review"] = dict(probe["access_review"], checked_at=stamp, actor_kind="machine", business_review_completed=False)
        if directory:
            config.update(include_paragraph_keywords=["中华人民共和国居民身份证法"], paragraph_expected_count=1,
                metadata_selectors={"page_title": {"class": "zt_xilan_03"}, "page_date_and_source": {"class": "zt_xilan_04"}},
                watch_scopes=[{"region": "郑州市二七区", "matter": "demo-id-renewal"}])
        else:
            config["include_articles"] = ["第三条", "第十一条", "第十九条", "第二十二条"]
            config["metadata_selectors"] = {"page_summary": {"class": "doc-info"}, "reported_validity": {"class": "yxx"}}
        chunks, _ = parse_html(raw, config["content_selector"], min_text_length=8 if directory else 1)
        chunks = [c for c in chunks if "中华人民共和国居民身份证法" in c["text"]] if directory else select_articles(chunks, config["include_articles"])
        if directory and (len(chunks) != 1 or chunks[0]["text"] != "39．中华人民共和国居民身份证法（2003年通过，2011年修正）"):
            raise ValueError("Inspected dated directory entry changed")
        metadata["source_observation"] = observe_html(raw, config["metadata_selectors"], content_selector=config["content_selector"])
        config["metadata"] = metadata
        validate_config(config)
        prepared[identifier] = (config, raw, chunks)
    old_draft = read(ROOT / "evaluation/knowledge/holdout-business-reference-draft.json")
    preserved = read(ROOT / "evaluation/knowledge/material-branch-assessments.json")["preserved_development_files"]
    baseline_path.write_text(json.dumps({"captured_at": stamp, "counts": store.status()["counts"], "law": previous_law,
        "old_holdout_content_sha256": old_draft["content_sha256"], "holdout_questions": [c["question_sha256"] for c in old_draft["cases"]],
        "policy_sha256": fingerprint((store.root / "publication-policy.json").read_bytes()),
        "old_law_source_config_sha256": old_source_hash, "old_law_paused_reason": old_source["paused_reason"],
        "preserved_development_files": preserved}, ensure_ascii=False, indent=2), encoding="utf-8")
    records = {}
    for identifier, (config, raw, chunks) in prepared.items():
        store.register(config)
        revision = store.ingest(identifier, SPECS[identifier]["url"], raw, config["metadata"], chunks)
        revision = store.exclude(revision, "Codex AI legal version identity review", SPECS[identifier]["reason"], confirmed=True)
        store.propose_classification(revision)
        records[identifier] = {"revision": revision, "url": SPECS[identifier]["url"], "original_sha256": fingerprint(raw),
            "normal_acquisition": True, "state": "excluded", "selected_chunks": len(chunks), "automatic_monitoring_enabled": config["enabled"]}
    metadata = dict(old_metadata, scope_confirmed=False, business_review="pending_full_text_and_current_applicability_review",
        selected_content_scope="All 23 articles re-extracted from the previously retained original; no new source download or approval.",
        extraction_provenance={"kind": "reextraction_of_existing_original", "previous_revision": BASE_LAW,
            "original_sha256": previous_law["original_sha256"], "content_selector": {"class": "content-txt"},
            "article_count": 23, "new_external_request": False, "performed_by": "Codex AI", "performed_at": stamp},
        legal_version_corroboration={"directory_revision": records["NATIONAL-LAW-DIRECTORY-2026"]["revision"],
            "directory_cutoff": "2026-08-28", "listed_revision_year": 2011, "current_after_cutoff_certified": False})
    expanded = store.ingest(document["site_id"], document["url"], raw_law, metadata, articles)
    store.propose_classification(expanded)
    records["EXISTING-ID-LAW-2011"] = {"revision": expanded, "previous_revision": BASE_LAW, "url": document["url"],
        "original_sha256": previous_law["original_sha256"], "normal_new_acquisition": False,
        "selected_chunks_before": 4, "selected_chunks": 23, "state": "pending_review", "source_stays_disabled_and_paused": True}
    schedule(store)
    for _ in range(3):
        with store.db() as db:
            job = db.execute("SELECT state FROM jobs WHERE site_id='NATIONAL-LAW-DIRECTORY-2026' ORDER BY id DESC LIMIT 1").fetchone()
        if not job or job["state"] in ("done", "failed"):
            break
        if not worker_once(store, owner="national-law-identity-intake"):
            break
    with store.db() as db:
        if not db.execute("SELECT 1 FROM jobs WHERE owner='national-law-identity-intake' AND state='running'").fetchone():
            db.execute("UPDATE workers SET progress='completed' WHERE id='national-law-identity-intake'")
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    (research / "intake-records.json").write_text(json.dumps({"captured_at": stamp, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"new_originals": 2, "existing_law_articles": 23, "records": records, "counts": store.status()["counts"],
                      "business_approval_performed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

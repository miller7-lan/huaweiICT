"""Record AI source/branch findings and a development-reference supplement.

Normal acquisition is a prerequisite. This does not review, evaluate or publish
any policy, and does not change frozen development references or model results.
"""

import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME


SOURCES = {
    "HN2026": ("HN-MEASURES-2026", "https://hnga.henan.gov.cn/2026/05-11/3349863.html"),
    "HN2025": ("HN-INTERPRETATION-DISCOVERY", "https://hnga.henan.gov.cn/2026/01-05/3282174.html"),
    "HN2024": ("HN-INTERPRETATION-DISCOVERY", "https://hnga.henan.gov.cn/2024/05-27/2998889.html"),
    "HN2023": ("HN-INTERPRETATION-DISCOVERY", "https://hnga.henan.gov.cn/2023/11-09/2844617.html"),
    "HN2022": ("HN-INTERPRETATION-DISCOVERY", "https://hnga.henan.gov.cn/2022/03-25/2420595.html"),
    "HN2020": ("HN-INTERPRETATION-DISCOVERY", "https://hnga.henan.gov.cn/2020/12-17/2002984.html"),
}
INSPECTED = {
    "HN2026": "rev-7a4b5d8dbcbbcb5614caf1f8", "HN2025": "rev-9768efea0e98a526c4940edf",
    "HN2024": "rev-36b17f5ac0618a9101119276", "HN2023": "rev-5018594a23160bda8d698b31",
    "HN2022": "rev-05d0ff35c89435b414aaefbc", "HN2020": "rev-8e05a4907bcbd74dacc95ba0",
}
EXCLUSIONS = {
    "HN2020": "Codex AI逐段检查本次归档15段，十项涉及户口迁移、出入境、交通、保安审批、律师和企业服务等；未列本试点身份证到期换领规则。排除试点回答，不能把其他证件绿色通道或周末受理移用于居民身份证。原件和目录监测继续保留，未判断整份措施失效。",
    "HN2022": "Codex AI逐段检查本次归档38段，事项为新生儿、犯罪记录、金融安防、保安许可、外国人居留、交通及平台服务；身份证在新生儿/机动车业务中用作材料，并非身份证换领政策。排除试点回答，不借用其他业务的时限或容缺规则。未判断整份措施失效；原HTML有图片内容未转录，未声称完成图片核查。",
}


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    stamp = datetime.now(timezone.utc).isoformat()
    records = {}
    for key, (source_id, url) in SOURCES.items():
        with store.db() as db:
            row = db.execute("SELECT * FROM revisions WHERE document_id=(SELECT id FROM documents WHERE site_id=? AND url=?) ORDER BY rowid DESC LIMIT 1", (source_id, url)).fetchone()
            if not row:
                raise ValueError("Normally acquired document missing")
            store._verify(db, row["id"], require_review=False)
        if key in EXCLUSIONS and row["id"] == INSPECTED[key]:
            revision = store.exclude(row["id"], "Codex AI source-scope analysis", EXCLUSIONS[key], confirmed=True)
            store.propose_classification(revision)
        else:
            meta = json.loads(row["metadata"])
            if row["id"] != INSPECTED[key] and not (key in EXCLUSIONS and meta.get("_excluded_after_revision") == INSPECTED[key] and row["state"] == "excluded"):
                raise ValueError("Source changed after inspection; separate review required")
            revision = row["id"]
        with store.db() as db:
            current, _ = store._verify(db, revision, require_review=False)
            _, chunks = store._content(db, revision)
        records[key] = {"source_id": source_id, "url": url, "revision": revision, "original_sha256": current["sha256"],
            "body_sha256": current["body_sha256"], "state": current["state"], "chunks": len(chunks),
            "observed_fields": json.loads(current["metadata"])["source_observation"]["fields"]}
    statements = [
        ("ordinary_duration", "省厅2026解读第一项明确本省户籍居民从受理到领证，由25自然日缩短至15工作日。", [("HN2026",0)]),
        ("exam_green_channel", "中考、高考特殊时期急需用证考生的绿色通道由15自然日缩短至5工作日，不是所有成年人急用均适用。", [("HN2026",0),("HN2026",1)]),
        ("explicit_later_duration_relationship", "2024解读载2021压缩至30日、2024再压缩至25日；2026解读直接说明25自然日再缩短至15工作日。不是把15与25作日历换算。", [("HN2024",0),("HN2024",1),("HN2026",1)]),
        ("voluntary_delivery", "2024解读在25日流程中载群众可选择自费邮寄；这不是额外加急费，也未给出本地现行邮费金额或解决所有收费文件关系。", [("HN2024",1)]),
        ("online_branch_and_condition_update", "2024郑州/焦作试点以两年等条件限定丢失补领、损坏换领；2025省厅解读列本省户籍、五年、申领时满16岁、指纹正常及对应丢失/损坏情形。不能把新条件当到期换领网办资格。", [("HN2024",4),("HN2024",6),("HN2024",7),("HN2025",0),("HN2025",2)]),
        ("nearby_service_limits", "2025解读将警务室服务与设备、培训及业务授权条件联系，不能据省级说明承诺二七所有警务室的地址、时段或现行受理条件。", [("HN2025",5),("HN2025",6)]),
        ("first_issue_and_temporary_branches", "2023措施首次申领与省内异地临时身份证分列，不是本地成年到期换领的一般材料替代清单。", [("HN2023",0),("HN2023",1),("HN2023",2),("HN2023",3)]),
    ]
    findings = []
    with store.db() as db:
        for identifier, text, refs in statements:
            anchors = []
            for key, ordinal in refs:
                binding = records[key]
                chunk = db.execute("SELECT locator,text FROM chunks WHERE revision_id=? AND ordinal=?", (binding["revision"], ordinal)).fetchone()
                anchors.append({"source_key": key, "revision": binding["revision"], "ordinal": ordinal, "locator": chunk["locator"],
                    "start": 0, "end": len(chunk["text"]), "parent_text_sha256": fingerprint(chunk["text"].encode()), "span_sha256": fingerprint(chunk["text"].encode())})
            findings.append({"id": identifier, "finding": text, "anchors": anchors, "business_release_approval": False})
    base = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
    preserved = json.loads((ROOT / "evaluation/knowledge/material-branch-assessments.json").read_text(encoding="utf-8"))["preserved_development_files"]
    for name, expected in preserved.items():
        if fingerprint((ROOT / name).read_bytes()) != expected:
            raise ValueError("Preserved development reference/result changed")
    assessment = {"schema": 1, "captured_at": stamp, "reviewer_kind": "ai", "reviewer": "Codex AI",
        "human_review_completed": False, "business_release_ready": False, "sources": records, "findings": findings,
        "source_scope_exclusions": EXCLUSIONS, "policy_file_sha256": fingerprint((store.root / "publication-policy.json").read_bytes()),
        "preserved_development_files": preserved, "collector_limitations": {"erqi_2026_page": "Normal Fetcher returned access denied; no original archived or bypass attempted", "xuchang_directory": "Normal Fetcher timed out; not registered as a working source"},
        "remaining": ["精确施行日及后续变更仍须区分；不把显示日、URL日或lastmodifiedtime当法律施行日", "本地材料及六个月调整的原始依据仍未解决", "独立业务评测和正式两端发布验收仍未完成"]}
    assessment["assessment_sha256"] = fingerprint(assessment)
    (ROOT / "evaluation/knowledge/duration-update-assessments.json").write_text(json.dumps(assessment, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    supplement = {"schema": 1, "kind": "source_based_development_reference_supplement", "captured_at": stamp,
        "base_reference": "evaluation/knowledge/pilot-ai-reference-answers.json", "base_file_sha256": fingerprint(base.read_bytes()),
        "source_assessment_sha256": assessment["assessment_sha256"], "author_kind": "ai", "human_review_completed": False,
        "dataset_role": "development_only_not_independent_acceptance", "ready_for_business_publication": False,
        "web_observations": [{"url": "https://www.erqi.gov.cn/xwfbh/9998921.jhtml", "displayed_date": "2026-04-15",
            "acquisition": "read_only_web_observation_normal_fetcher_access_denied_no_local_original", "not_a_policy_commencement": True}],
        "case_updates": [{"id": "KQ13", "question": "受理以后多久能领新身份证，工作日和自然日怎么计算？",
            "ai_reference_answer": "应优先说明新取得的省公安厅2026政策解读：本省户籍居民办证从受理之日起到领证之日，由25个自然日缩短至15个工作日；中考、高考特殊时期急需用证考生的绿色通道另由15个自然日缩短至5个工作日，普通成年人急用不能自动套用。2024解读记录此前30日至25日的调整，法律与国家规范另有时限层次，不能把各数字直接换算或选最短数值保证领证。该2026页面显示2026-05-05，修改标记及URL目录为2026-05-11，二七政府另有2026-04-15介绍；这些不是已核定的精确施行日。具体当前受理承诺及后续变化仍须核对。",
            "archived_evidence": [{"revision": records[key]["revision"], "locator": "正文段落"+str(i+1)} for key in ("HN2026", "HN2024") for i in (0,1)],
            "unresolved": assessment["remaining"][:2], "tested_agent_output_used_as_reference": False}],
        "usage": "Apply this source-based update when preparing the next complete reviewed reference; do not silently rerun the old 30-question development model trial or overwrite any frozen baseline/result."}
    supplement["content_sha256"] = fingerprint(supplement)
    (ROOT / "evaluation/knowledge/duration-reference-supplement.json").write_text(json.dumps(supplement, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"sources": len(records), "findings": len(findings), "anchors": sum(len(f["anchors"]) for f in findings), "scope_exclusions": len(EXCLUSIONS), "counts": store.status()["counts"], "model_generations": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

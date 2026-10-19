"""Build explicitly synthetic rule/evidence fixtures; no real policy is approved."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.decision.models import KnowledgeSnapshot, reviewed_hash


def build_fixture():
    lines = {
        "core": [
            "【工程模拟资料，不是真实政务政策】",
            "模拟市通行卡到期换领分为申请和领取两个环节。",
            "成年本人到期换领可进入成年本人演练分支。",
            "未成年人本人情况单独分支，本模拟资料尚未提供其办事清单。",
            "成年本人演练分支内，申请时需提交模拟申请表。",
            "成年本人演练分支内，申请时应交验演练旧卡。",
            "成年本人演练分支内，领取新卡时交回演练旧卡。",
            "成年本人演练分支内，领取时需出示模拟领取凭条。",
            "演练旧卡又称旧演练卡，本演练中为同一材料。",
            "本模拟规则仅供模拟市演练，时间区间为2026-01-01至2027-01-01（不含后者）。",
        ],
        "exception": [
            "【工程模拟资料，不是真实政务政策】",
            "成年本人演练分支中，可验证的模拟电子申请表替代模拟纸质申请表；无法验证时仍需纸质表。",
            "电子表是否可验证不清楚时，不得推定有或没有替代资格。",
        ],
        "unconfirmed": [
            "【工程模拟资料，不是真实政务政策】",
            "某演练资料提到申请时可出示模拟居住证明，该说法是否适用于模拟市尚未确认。",
            "另有演练资料把演练旧卡简称为旧证，该别名尚未确认。",
        ],
    }
    assets, spans = [], []
    def ev(source, line_number, evidence_id):
        text = "\n".join(lines[source]) + "\n"
        quote = lines[source][line_number]
        start = text.index(quote)
        spans.append({"id": evidence_id, "asset_id": "asset-" + source, "start": start, "end": start + len(quote),
                      "quote": quote, "locator": f"模拟文本第{line_number + 1}行"})
        return evidence_id
    for key in lines:
        text = "\n".join(lines[key]) + "\n"
        assets.append({"id":"asset-" + key, "source_url":"synthetic://gov-demo/" + key,
                       "title":"模拟通行卡资料-" + key, "revision":"rev-" + key + "-v1", "data_class":"synthetic",
                       "text":text, "sha256":hashlib.sha256(text.encode("utf-8")).hexdigest()})
    ev("core", 1, "ev-reason")
    ev("core", 2, "ev-adult")
    ev("core", 3, "ev-minor")
    ev("core", 4, "ev-paper")
    ev("core", 5, "ev-card-apply")
    ev("core", 6, "ev-card-collect")
    ev("core", 7, "ev-slip")
    ev("core", 8, "ev-alias")
    ev("core", 9, "ev-scope")
    ev("exception", 1, "ev-substitution")
    ev("exception", 2, "ev-substitution-unknown")
    ev("unconfirmed", 1, "ev-residence")
    ev("unconfirmed", 2, "ev-alias-candidate")
    reviews = []
    def reviewed(item, confirmed=True):
        item = {**item, "review_status":"confirmed" if confirmed else "candidate", "review_id":None}
        if confirmed:
            review_id = "review-" + item["id"]
            item["review_id"] = review_id
            reviews.append({"id":review_id, "object_id":item["id"], "object_sha256":"0" * 64,
                            "actor_id":"fixture-professional", "actor_kind":"simulated_professional", "decision":"accepted",
                            "note":"仅为工程模拟中的已确认样本，不代表真实人员审阅或真实政策发布。"})
        return item
    def eq(field, value): return {"op":"eq", "field":field, "value":value}
    conditions = [
        reviewed({"id":"cond-expiry", "expression":eq("reason","expiry"), "evidence_ids":["ev-reason"]}),
        reviewed({"id":"cond-adult", "expression":{"op":"all","args":[eq("age_group","adult"),eq("self_service",True)]}, "evidence_ids":["ev-adult"]}),
        reviewed({"id":"cond-minor", "expression":{"op":"all","args":[eq("age_group","minor"),eq("self_service",True)]}, "evidence_ids":["ev-minor"]}),
        reviewed({"id":"cond-unconditional", "expression":{"op":"const","value":True}, "evidence_ids":["ev-adult"]}),
        reviewed({"id":"cond-paper", "expression":{"op":"not","arg":eq("electronic_verified",True)}, "evidence_ids":["ev-paper","ev-substitution","ev-substitution-unknown"]}),
        reviewed({"id":"cond-electronic", "expression":eq("electronic_verified",True), "evidence_ids":["ev-substitution","ev-substitution-unknown"]}),
    ]
    branches = [
        reviewed({"id":"branch-expiry", "label":"到期换领", "parent_id":None,"condition_id":"cond-expiry","order":0,"evidence_ids":["ev-reason"]}),
        reviewed({"id":"branch-adult-self", "label":"成年本人", "parent_id":"branch-expiry","condition_id":"cond-adult","order":0,"evidence_ids":["ev-adult"]}),
        reviewed({"id":"branch-minor-self", "label":"未成年人本人（资料待补齐）", "parent_id":"branch-expiry","condition_id":"cond-minor","order":1,"evidence_ids":["ev-minor"]}),
    ]
    applicability = [
        reviewed({"id":"scope-main", "region_id":"demo-city","clause_ids":["clause-main"],
                  "window":{"confirmed":True,"start_inclusive":"2026-01-01","end_exclusive":"2027-01-01"},"evidence_ids":["ev-scope"]}),
        reviewed({"id":"scope-residence", "region_id":"demo-city","clause_ids":["clause-residence"],
                  "window":{"confirmed":False,"start_inclusive":None,"end_exclusive":None},"evidence_ids":["ev-residence"]},False),
    ]
    def requirement(key, stage, material, action, condition, evidence, *, candidate=False):
        return reviewed({"id":"req-" + key,"branch_id":"branch-adult-self","stage_id":stage,"material_id":material,
                         "action":action,"condition_id":condition,"applicability_id":"scope-residence" if candidate else "scope-main",
                         "evidence_ids":evidence}, not candidate)
    requirements = [
        requirement("paper","application","material-paper","提交","cond-paper",["ev-paper","ev-substitution"]),
        requirement("electronic","application","material-electronic","提交","cond-electronic",["ev-substitution"]),
        requirement("card-apply","application","material-old-card","交验","cond-unconditional",["ev-card-apply"]),
        requirement("card-collect","collection","material-old-card","交回","cond-unconditional",["ev-card-collect"]),
        requirement("slip","collection","material-slip","出示","cond-unconditional",["ev-slip"]),
        requirement("residence","application","material-residence","出示","cond-unconditional",["ev-residence"],candidate=True),
    ]
    aliases = [
        reviewed({"id":"alias-old-card","material_id":"material-old-card","label":"旧演练卡","evidence_ids":["ev-alias"]}),
        reviewed({"id":"alias-unconfirmed","material_id":"material-old-card","label":"旧证","evidence_ids":["ev-alias-candidate"]},False),
    ]
    result = {"schema_version":1,"mode":"synthetic_engineering_only","demo_date":"2026-10-17",
            "versions":{"documents":"demo-docs-v1","knowledge":"demo-knowledge-v1","ontology":"demo-ontology-v1"},
            "matter":{"id":"matter-demo-pass","label":"模拟通行卡到期换领","region_id":"demo-city"},
            "fields":[{"id":"reason","label":"办理原因","kind":"enum","options":["expiry","lost"]},
                      {"id":"age_group","label":"年龄组","kind":"enum","options":["adult","minor"]},
                      {"id":"self_service","label":"是否本人办理","kind":"boolean","options":[]},
                      {"id":"electronic_verified","label":"电子申请表是否可验证","kind":"boolean","options":[]}],
            "stages":[{"id":"application","label":"申请","order":0},{"id":"collection","label":"领取","order":1}],
            "materials":[{"id":"material-paper","label":"模拟纸质申请表"},{"id":"material-electronic","label":"模拟电子申请表"},
                         {"id":"material-old-card","label":"演练旧卡"},{"id":"material-slip","label":"模拟领取凭条"},
                         {"id":"material-residence","label":"模拟居住证明"}],
            "assets":assets,"evidence":spans,
            "clauses":[{"id":"clause-main","label":"模拟主文件的适用范围","evidence_ids":["ev-scope"]},
                       {"id":"clause-residence","label":"模拟待确认资料","evidence_ids":["ev-residence"]}],
            "conditions":conditions,"branches":branches,"applicability":applicability,
            "requirements":requirements,"aliases":aliases,"reviews":reviews}
    reviewed_objects = {item["id"]:item for rows in [conditions,branches,applicability,requirements,aliases] for item in rows}
    for review in reviews:
        review["object_sha256"] = reviewed_hash(reviewed_objects[review["object_id"]],result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"config/decision/synthetic.snapshot.json")
    args = parser.parse_args()
    snapshot = KnowledgeSnapshot.model_validate_json(json.dumps(build_fixture(),ensure_ascii=False))
    raw = snapshot.model_dump_json(indent=2) + "\n"
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists() and args.output.read_text(encoding="utf-8") != raw:
        raise ValueError("EXISTING_FIXTURE_DIFFERS: choose a new output/version instead of overwriting")
    args.output.write_text(raw,encoding="utf-8")
    schema = args.output.with_name("snapshot.schema.json")
    schema.write_text(json.dumps(KnowledgeSnapshot.model_json_schema(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"mode":snapshot.mode,"sha256":snapshot.json_fingerprint(),"requirements":len(snapshot.requirements),
                      "simulated_reviews":len(snapshot.reviews)},ensure_ascii=False))


if __name__=="__main__":
    main()

"""Exercise the offline decision foundation with explicitly synthetic materials."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from gov_mcp.decision import DecisionService
from gov_mcp.decision.service import MAINTENANCE_MESSAGE
from private_runtime import ensure_private_directory

FIXTURE = ROOT/"config/decision/synthetic.snapshot.json"
FACTS = {"reason":"expiry","age_group":"adult","self_service":True,"electronic_verified":False}


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def all_items(answer):
    return [item for stage in answer["stages"] for item in stage["items"]]


def ids(answer):
    return {row["requirement_id"]for row in all_items(answer)}


def make_markdown(examples, checks):
    lines=["# Agent 第一阶段：模拟规则演练", "", "全部资料和复核记录均为工程模拟；未调用模型、Jev 或官网。", "",
           "本阶段验证知识对象、条件判定、原文关联和返回前暂停检查，三角色 Agent 编排及前端页面尚待后续接入。", ""]
    for label,answer in examples.items():
        lines.extend(["## "+label,"",f"状态：`{answer['status']}`；已确认 {answer['counts']['confirmed']}，待确认 {answer['counts']['pending']}，暂停 {answer['counts']['paused']}。", "",
                      "| 环节 | 材料 | 提供动作 | 原文依据 |","| --- | --- | --- | --- |"])
        for stage in answer["stages"]:
            for item in stage["items"]:
                direct=[e for e in item["evidence"] if e["id"] in next(edge["evidence_ids"]for edge in item["path"]if edge["relation"]=="has_requirement")]
                quotes="；".join(e["quote"]for e in direct)
                lines.append(f"| {stage['label']} | {item['material']} | {item['action']} | {quotes} |")
        if answer["follow_up"]["enabled"]:
            lines.extend(["","可选择“补充情况”："+"、".join(field["label"]for field in answer["follow_up"]["fields"])])
        if answer["paused"]:
            lines.extend(["",MAINTENANCE_MESSAGE+"。仅暂停受影响项目，其余内容见上表。"])
        if answer["pending"]:
            lines.extend(["","待确认："+"、".join(row["material"]for row in answer["pending"])+"。这些项目未加入确定清单。"])
        lines.extend(["",f"资料版本：`{answer['versions']['documents']}`；知识快照：`{answer['snapshot_sha256']}`。",""])
    lines.extend(["## 检查结果","",f"{sum(checks.values())}/{len(checks)} 项通过。",""])
    for name,passed in checks.items():lines.append(f"- {'通过' if passed else '失败'}：{name}")
    return "\n".join(lines)+"\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--facts",help="Run one custom JSON facts object; output stays private")
    group.add_argument("--facts-file",type=Path,help="Read a facts JSON file without shell quoting; output stays private")
    parser.add_argument("--branch",default="branch-adult-self")
    parser.add_argument("--as-of",help="Explicit ISO date; defaults to the fixed simulation date")
    args=parser.parse_args()
    custom=bool(args.facts is not None or args.facts_file is not None or args.as_of or args.branch!="branch-adult-self")
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    runtime=ROOT/".runtime/decision-demo"/run_id
    ensure_private_directory(runtime)
    report={"captured_at":datetime.now(timezone.utc).isoformat(),"mode":"synthetic_engineering_only",
            "model_calls":0,"real_policy_advice":False,"agents_executed":False,"passed":False}
    try:
        service=DecisionService.from_file(FIXTURE)
        if custom:
            if args.facts_file is not None:
                if args.facts_file.stat().st_size>32768:raise ValueError("FACTS_FILE_TOO_LARGE")
                facts=json.loads(args.facts_file.read_bytes())
            else:
                facts=json.loads(args.facts)if args.facts is not None else FACTS
            as_of=date.fromisoformat(args.as_of)if args.as_of else None
            answer=service.answer(facts,args.branch,as_of=as_of)
            write_json(runtime/"answer.json",answer)
            print(json.dumps({"status":answer["status"],"counts":answer["counts"],"answer_file":str(runtime/"answer.json")},ensure_ascii=False))
            return
        paper=service.answer(FACTS,"branch-adult-self")
        electronic=service.answer({**FACTS,"electronic_verified":True},"branch-adult-self")
        unknown=service.answer({**FACTS,"electronic_verified":None},"branch-adult-self")
        task=service.prepare(FACTS,"branch-adult-self")
        service.availability.suspend("ev-card-apply",change_id="demo-conflict",reason="模拟申请环节交验要求发生冲突")
        paused=service.finalize(task)
        unsupported=DecisionService.from_file(FIXTURE).answer({**FACTS,"age_group":"minor"},"branch-minor-self")
        incomplete=DecisionService.from_file(FIXTURE).answer({"reason":"expiry"},"branch-adult-self")
        examples={"电子表不可验证：采用纸质表":paper,"电子表可验证：采用电子表":electronic,
                  "电子表状态未知：保留独立已确认要求":unknown,"生成途中发现冲突：返回前暂停":paused}
        providers=json.loads((ROOT/"config/decision/providers.template.json").read_bytes())
        checks={
            "same_material_stage_specific_actions":{(r["stage_id"],r["action"])for r in all_items(paper)if r["material_id"]=="material-old-card"}=={("application","交验"),("collection","交回")},
            "paper_selected_when_electronic_unverified":"req-paper"in ids(paper)and"req-electronic"not in ids(paper),
            "electronic_selected_without_paper":"req-electronic"in ids(electronic)and"req-paper"not in ids(electronic),
            "unknown_exception_does_not_force_paper":not {"req-paper","req-electronic"}&ids(unknown),
            "unknown_preserves_independent_items":unknown["counts"]["confirmed"]==3,
            "optional_followup_only_for_missing_condition":unknown["follow_up"]["optional"]and[field["id"]for field in unknown["follow_up"]["fields"]]==["electronic_verified"],
            "unconfirmed_fact_only_in_pending":"req-residence"not in ids(paper)and"req-residence"in{r["requirement_id"]for r in paper["pending"]},
            "multiple_document_evidence_present":{e["asset_id"]for r in all_items(paper)if r["requirement_id"]=="req-paper"for e in r["evidence"]}>={"asset-core","asset-exception"},
            "all_business_edges_have_evidence":all(edge["evidence_ids"]for row in all_items(paper)for edge in row["path"]),
            "fixed_snapshot_current_suspension_checked":paused["snapshot_sha256"]==paper["snapshot_sha256"]and{r["requirement_id"]for r in paused["paused"]}=={"req-card-apply"},
            "unaffected_collection_still_available":"req-card-collect"in ids(paused),
            "configured_public_maintenance_message":paused["paused"][0]["message"]==MAINTENANCE_MESSAGE,
            "candidate_alias_not_applied":[a["label"]for a in paper["confirmed_aliases"]]==["旧演练卡"],
            "uncovered_branch_no_adult_fallback":unsupported["status"]=="no_confirmed_requirements"and not ids(unsupported),
            "branch_selection_does_not_invent_facts":not ids(incomplete)and incomplete["branch_result"]=="unknown",
            "api_fields_remain_empty":not providers["enabled"]and all(value is None for role in providers["roles"].values()for value in role.values()),
        }
        code_paths=["src/gov_mcp/decision/logic.py","src/gov_mcp/decision/models.py","src/gov_mcp/decision/service.py",
                    "scripts/build_decision_fixture.py","scripts/run_decision_demo.py"]
        report.update(snapshot_sha256=paper["snapshot_sha256"],fixture_file_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                      source_files_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()for p in code_paths},
                      checks=checks,passed=all(checks.values()),examples={label:{"counts":value["counts"],"status":value["status"]}for label,value in examples.items()})
        for index,(label,answer)in enumerate(examples.items(),1):write_json(runtime/f"example-{index}.json",answer)
        markdown=make_markdown(examples,checks)
        (runtime/"模拟规则演练.md").write_text(markdown,encoding="utf-8")
        public=ROOT/"evidence/decision-foundation"
        public.mkdir(parents=True,exist_ok=True)
        write_json(public/"verification.json",report)
        for index,answer in enumerate(examples.values(),1):write_json(public/f"example-{index}.json",answer)
        (public/"模拟规则演练.md").write_text(markdown,encoding="utf-8")
        write_json(runtime/"verification.json",report)
        print(json.dumps({"passed":report["passed"],"checks":f"{sum(checks.values())}/{len(checks)}","model_calls":0,"report":str(public/"模拟规则演练.md")},ensure_ascii=False))
        if not report["passed"]:raise SystemExit(1)
    except Exception as error:
        report.update(failure_class=type(error).__name__,passed=False)
        write_json(runtime/"verification.json",report)
        if not custom:write_json(ROOT/"evidence/decision-foundation/verification.json",report)
        raise


if __name__=="__main__":
    main()

"""Summarize frozen three-arm results; never call models or alter raw records."""
import json
from collections import Counter
from pathlib import Path
import sys


def analyze(folder):
    root=Path(folder)
    load=lambda name:json.loads((root/name).read_text(encoding="utf-8-sig"))
    summary=load("summary.json")
    records=load("records.json")
    judges=load("judgments.json")
    calls=load("calls.json")
    jev=load("jev_calls.json")
    valid=set(summary["quality_scored_ids"])
    scores={}
    for item in judges:
        if item["id"] not in valid:continue
        for r in item["ratings"]:
            arm=item["private_key"][r["label"]]
            scores.setdefault(item["id"],{}).setdefault(arm,[]).append(r["score"])
    paired={qid:{arm:sum(v)/len(v) for arm,v in rows.items()} for qid,rows in scores.items()}
    stages={}
    for stage in sorted({r["stage"] for r in calls}):
        rows=[r for r in calls if r["stage"]==stage]
        stages[stage]={"attempts":len(rows),"successes":sum(r["status"]=="ok" for r in rows),
            "successful_usage":{k:sum(r.get("usage",{}).get(k,0) for r in rows) for k in ("input_tokens","output_tokens")},
            "duration_ms":sum(r["duration_ms"] for r in rows)}
    report={"summary":summary,"paired_mean_scores_0_to_2":paired,"by_stage":stages,
        "stop_reasons":{arm:dict(Counter(r["final"]["stop_reason"] for r in records if r["arm"]==arm)) for arm in summary["by_arm"]},
        "jev_actual_http_requests":sum(len(r.get("receipts",[])) for r in jev),
        "jev_decisions":[{"id":r["id"],"stage":r["stage"],"status":r["status"],
            "action":r.get("answers",{}).get("action"),"route":r.get("answers",{}).get("route"),
            "supported":r.get("answers",{}).get("supported"),
            "error_codes":[receipt.get("error_code") for receipt in r.get("receipts",[]) if receipt.get("error_code")]} for r in jev],
        "jev_successful_usage":{k:sum(r.get("usage",{}).get(k,0) for r in jev) for k in ("input_tokens","output_tokens")},
        "failed_call_usage_unknown":any(r["status"]!="ok" for r in calls+jev)}
    (root/"analysis.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":analyze(sys.argv[1])

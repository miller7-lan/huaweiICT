"""Privately convert completed blank-template PDFs to the existing review CSV contract."""
import argparse
import csv
import hashlib
from pathlib import Path
from pypdf import PdfReader

PACK_SHA="f3cb4af4bdce27a6a0a1c6544dad0afed0821ddc9834e4a4f1868daa84c76582"
BATCH="flow-v3-r2-20261114"
MAPS={
    "conclusions_correct":{"Y":"是","N":"否","P":"部分","NA":"不适用"},
    "missing_points":{"NO":"否","MINOR":"漏次要","CORE":"漏核心","NA":"不适用"},
    "citations_support":{"Y":"是","N":"否","NA":"不适用"},
    "excerpt_relevant":{"Y":"是","N":"否","NONE":"无摘录"},
    "stop_judgment":{"Y":"合理","N":"不合理","U":"待确认","NA":"不适用"},
    "reference_issue":{"NO":"未发现","Y":"有误","U":"待确认"},
}
HEADERS=["question_id","label","reviewer","conclusions_correct","missing_points","citations_support","excerpt_relevant","remark"]

def value(fields,name):
    item=fields.get(name)
    if item is None:raise ValueError("MISSING_FORM_FIELD: "+name)
    raw=item.get("/V")
    if raw is None:raise ValueError("UNFILLED_FIELD: "+name)
    raw=str(raw).removeprefix("/")
    if raw in ("Off",""):raise ValueError("UNFILLED_FIELD: "+name)
    return raw

def read_rows(path,reviewer,pack):
    reader=PdfReader(path)
    meta=reader.metadata or {}
    if meta.get("/ReviewerID")!=reviewer:raise ValueError("REVIEWER_MISMATCH")
    if meta.get("/ReviewBatchID")!=BATCH or meta.get("/ReviewFormVersion")!="1":raise ValueError("FORM_VERSION_MISMATCH")
    actual=hashlib.sha256((Path(pack)/"review.md").read_bytes()).hexdigest()
    if meta.get("/ReviewPackSHA256")!=PACK_SHA or actual!=PACK_SHA:raise ValueError("PRIVATE_PACK_MISMATCH")
    fields=reader.get_fields() or {}
    expected={f"{reviewer}.{qid}.{label}.{key}" for qid in [f"N{i:02}" for i in range(1,9)] for label in "AB" for key in MAPS}
    expected|={f"{reviewer}.confirm.{k}" for k in ("independent","all_rows","saved")}
    if set(fields)!=expected:raise ValueError("FORM_FIELDS_MISMATCH_OR_FLATTENED")
    for name in ("independent","all_rows","saved"):
        if value(fields,f"{reviewer}.confirm.{name}")!="Yes":raise ValueError("COMPLETION_NOT_CONFIRMED")
    rows=[]
    for i in range(1,9):
        qid=f"N{i:02}"
        for label in "AB":
            choices={}
            for key,allowed in MAPS.items():
                selected=value(fields,f"{reviewer}.{qid}.{label}.{key}")
                if selected not in allowed:raise ValueError("INVALID_CHOICE: "+qid+label+"/"+key)
                choices[key]=selected
            is_stop=choices["stop_judgment"]!="NA"
            core=[choices[k]=="NA" for k in ("conclusions_correct","missing_points","citations_support")]
            if is_stop and (not all(core) or choices["excerpt_relevant"]!="NONE"):raise ValueError("STOP_ROW_RULE_MISMATCH: "+qid+label)
            if not is_stop and any(core):raise ValueError("ANSWER_ROW_RULE_MISMATCH: "+qid+label)
            rows.append(dict(question_id=qid,label=label,reviewer=reviewer,
                **{key:MAPS[key][choices[key]] for key in list(MAPS)[:4]},
                remark="停止判断="+MAPS["stop_judgment"][choices["stop_judgment"]]+"；参考答案="+MAPS["reference_issue"][choices["reference_issue"]]))
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pdf",type=Path,required=True)
    p.add_argument("--reviewer",choices=("reviewer1","reviewer2"),required=True)
    p.add_argument("--pack",type=Path,required=True,help="Private organizer pack holding review.md")
    p.add_argument("--output",type=Path,required=True,help="New private CSV under this repository's .runtime")
    a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if not a.output.resolve().is_relative_to((root/".runtime").resolve()) or a.output.exists():p.error("NEW_PRIVATE_RUNTIME_CSV_REQUIRED")
    try:rows=read_rows(a.pdf,a.reviewer,a.pack)
    except ValueError as error:p.error(str(error))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open("x",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=HEADERS);writer.writeheader();writer.writerows(rows)
    print(f"Converted {len(rows)} rows for {a.reviewer}; keep this CSV private.")

if __name__=="__main__":main()

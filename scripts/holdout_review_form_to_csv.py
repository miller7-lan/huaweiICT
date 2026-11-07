"""Turn a returned phone-filled review PDF or web answers JSON into the standard score sheet CSV.

Reads the radio and text fields written by build_holdout_review_form.py (or the
answers file saved by holdout_review_web.py, same field names and codes), copies
the first six columns of the package's blank sheet unchanged and writes
评分表-<run_id>-<reviewer>.csv plus 参考答案问题清单-<run_id>-<reviewer>.csv.
Empty or invalid cells and missing remarks are listed so the reviewer can
complete them; nothing is guessed or filled in. Only counts and cell names are
printed, never the ratings themselves.
"""
import argparse
import csv
import json
from pathlib import Path

from pypdf import PdfReader

COLUMNS = {"ok": ("结果可接受(是/否)", {"Y": "是", "N": "否"}),
           "cov": ("关键断言覆盖(全部/部分/无/不适用)", {"all": "全部", "part": "部分", "none": "无", "na": "不适用"}),
           "err": ("有事实错误(是/否)", {"Y": "是", "N": "否"}),
           "cite": ("引用支撑结论(是/否/不适用)", {"Y": "是", "N": "否", "na": "不适用"}),
           "ban": ("触犯禁止项(是/否)", {"Y": "是", "N": "否"}),
           "score": ("总评(2/1/0)", {"s2": "2", "s1": "1", "s0": "0"})}
NEEDS_REMARK = {"结果可接受(是/否)": {"否"}, "引用支撑结论(是/否/不适用)": {"否"}, "有事实错误(是/否)": {"是"},
                "触犯禁止项(是/否)": {"是"}, "总评(2/1/0)": {"1", "0"}}


def text(value):
    return str(value).lstrip("/") if value is not None else ""


def form_values(reader):
    """Field name -> value; a radio group whose parent lacks /V falls back to its checked widget."""
    values = {}
    for ref in reader.trailer["/Root"]["/AcroForm"]["/Fields"]:
        field = ref.get_object()
        value = text(field.get("/V"))
        if value in ("", "Off"):
            states = [text(kid.get_object().get("/AS")) for kid in field.get("/Kids", [])]
            value = next((s for s in states if s not in ("", "Off")), "")
        values[text(field.get("/T"))] = value.strip()
    return values


def read_answers(path):
    """Values, run id and embedded reviewer from a filled PDF form or a web answers JSON."""
    if Path(path).suffix.lower() == ".json":
        record = json.loads(Path(path).read_text("utf-8"))
        values = {k: v.strip() for k, v in record["values"].items() if isinstance(v, str)}
        return values, record.get("run_id", ""), record.get("reviewer", "")
    reader = PdfReader(path)
    meta = reader.metadata or {}
    return form_values(reader), text(meta.get("/HoldoutRunId")), text(meta.get("/HoldoutReviewer"))


def convert(pdf, package, reviewer=None, out_dir=None, overwrite=False):
    values, run_id, embedded = read_answers(pdf)
    if reviewer and embedded and reviewer != embedded:
        raise SystemExit("REVIEWER_DIFFERS_FROM_FORM")
    reviewer = reviewer or embedded
    if not reviewer:
        raise SystemExit("REVIEWER_REQUIRED: the form lost its metadata, pass --reviewer")
    blank = package / f"评分表-{run_id}.csv" if run_id else next(package.glob("评分表-*.csv"))
    run_id = run_id or blank.stem.split("-", 1)[1]
    with blank.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    missing, remarks, filled = [], [], []
    for row in body:
        qid, label = row[0], row[4]
        cells = {}
        for suffix, (column, mapping) in COLUMNS.items():
            cells[column] = mapping.get(values.get(f"{qid}_{label}_{suffix}", ""), "")
            if not cells[column]:
                missing.append(f"{qid}{label} {column.split('(')[0]}")
        note = values.get(f"{qid}_{label}_note", "")
        if not note and any(cells[c] in v for c, v in NEEDS_REMARK.items()):
            remarks.append(f"{qid}{label}")
        filled.append(row[:6] + [cells[c] for c, _ in COLUMNS.values()] + [reviewer, note])
    if [r[:6] for r in filled] != [r[:6] for r in body] or len({(r[0], r[4]) for r in filled}) != len(body):
        raise SystemExit("FIRST_SIX_COLUMNS_OR_ROWS_CHANGED")
    issues = [[qid, values.get(f"{qid}_ref_issue", ""), values.get(f"{qid}_ref_basis", "")]
              for qid in dict.fromkeys(r[0] for r in body)]
    issues = [i for i in issues if i[1] or i[2]]
    out_dir = Path(out_dir or Path(pdf).parent)
    sheet = out_dir / f"评分表-{run_id}-{reviewer}.csv"
    issue_file = out_dir / f"参考答案问题清单-{run_id}-{reviewer}.csv"
    for path in (sheet, issue_file):
        if path.exists() and not overwrite:
            raise SystemExit("OUTPUT_EXISTS_NOT_OVERWRITTEN: " + str(path))
    with sheet.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows([header] + filled)
    with issue_file.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows([["题号", "问题", "原文依据（来源编号+定位）"]] + issues)
    complete = not missing and not remarks
    return {"status": "COMPLETE" if complete else "INCOMPLETE_RETURN_TO_REVIEWER", "run_id": run_id,
            "reviewer": reviewer, "rows": len(filled), "empty_cells": len(missing), "empty_cell_list": missing,
            "remarks_required_but_empty": remarks, "reference_issues": len(issues),
            "sheet": str(sheet), "issue_list": str(issue_file)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Returned filled PDF, or the web answers JSON")
    parser.add_argument("--package", type=Path, required=True, help="Unzipped ict-holdout-review-<run_id> folder")
    parser.add_argument("--reviewer", help="Only needed when the returned PDF lost its metadata")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = convert(args.pdf, args.package, args.reviewer, args.out_dir, args.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

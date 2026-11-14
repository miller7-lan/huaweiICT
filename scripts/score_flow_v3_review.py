"""Unblind two reviewers' sheets and print counts only (safe to commit).

Run after both sheets are complete. Reads key.json from the pack directory.
The output holds, per method and column, how many rows took each value, how
often the two reviewers agree, and the question ids where they differ. It
holds no question, answer or remark text. When both sheets carry the PDF
form's two extra judgements in the remark column, stop judgements and
reference-answer flags are counted too; other remark text is never read.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_flow_v3_review_pack import CHOICES, EXTRA_CHOICES  # noqa: E402

# Two extra judgements that the fillable PDF form stores in the remark column as
# "停止判断=<value>；参考答案=<value>". Only these fixed values are read; free text is ignored.
EXTRA = {"stop_judgment": ("停止判断", ("合理", "不合理", "待确认", "不适用")),
         "reference_issue": ("参考答案", ("未发现", "有误", "待确认"))}


def extras(remark):
    found = {}
    for part in (remark or "").split("；"):
        name, _, value = part.partition("=")
        for column, (label, allowed) in EXTRA.items():
            if name.strip() == label and value.strip() in allowed:
                found[column] = value.strip()
    return found


def read_sheet(path, key):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected = {(q, label) for q, pair in key.items() for label in pair}
    if {(r["question_id"], r["label"]) for r in rows} != expected or len(rows) != len(expected):
        raise ValueError("SHEET_ROWS_DO_NOT_MATCH_KEY: " + str(path))
    for row in rows:
        for column, allowed in CHOICES.items():
            if row[column].strip() not in allowed:
                raise ValueError(f"INVALID_VALUE: {path} {row['question_id']}{row['label']} {column}")
    result = {}
    for row in rows:
        values = {**{c: row[c].strip() for c in CHOICES}, **extras(row.get("remark"))}
        for column, allowed in EXTRA_CHOICES.items():  # a real column wins over the remark
            if (row.get(column) or "").strip():
                if row[column].strip() not in allowed:
                    raise ValueError(f"INVALID_VALUE: {path} {row['question_id']}{row['label']} {column}")
                values[column] = row[column].strip()
        result[(row["question_id"], row["label"])] = values
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--sheets", type=Path, nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file under evidence/")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
    key = json.loads((args.pack / "key.json").read_text("utf-8"))
    sheets = [read_sheet(path, key) for path in args.sheets]
    methods = sorted({method for pair in key.values() for method in pair.values()})
    by_method = {method: {c: Counter() for c in CHOICES} for method in methods}
    agree, differ = {c: 0 for c in CHOICES}, {c: [] for c in CHOICES}
    for question_id, pair in sorted(key.items()):
        for label, method in pair.items():
            first, second = (sheet[(question_id, label)] for sheet in sheets)
            for column in CHOICES:
                by_method[method][column][first[column]] += 1
                by_method[method][column][second[column]] += 1
                if first[column] == second[column]:
                    agree[column] += 1
                else:
                    differ[column].append(f"{question_id}{label}")
    rows = sum(len(pair) for pair in key.values())
    # Stop judgements are per output; a reference issue is per question, whoever raised it.
    stops = {m: Counter() for m in by_method}
    stop_agree, stop_differ, reference = 0, [], {value: set() for value in EXTRA["reference_issue"][1]}
    has_extras = all("stop_judgment" in row and "reference_issue" in row for sheet in sheets for row in sheet.values())
    if has_extras:
        for question_id, pair in sorted(key.items()):
            for label, method in pair.items():
                first, second = (sheet[(question_id, label)] for sheet in sheets)
                stops[method][first["stop_judgment"]] += 1
                stops[method][second["stop_judgment"]] += 1
                if first["stop_judgment"] == second["stop_judgment"]:
                    stop_agree += 1
                else:
                    stop_differ.append(f"{question_id}{label}")
                for row in (first, second):
                    reference[row["reference_issue"]].add(question_id)
    result = {"scope": "counts and question ids only", "questions": len(key), "rows_per_sheet": rows,
              "methods": methods, "ratings_per_method_and_column": len(key) * 2,
              "by_method": {m: {c: dict(sorted(n.items())) for c, n in cols.items()} for m, cols in by_method.items()},
              "reviewer_agreement": {c: f"{agree[c]}/{rows}" for c in CHOICES},
              "disagreements": {c: sorted(v) for c, v in differ.items()}}
    if has_extras:
        result["stop_judgment_by_method"] = {m: dict(sorted(n.items())) for m, n in stops.items()}
        result["stop_judgment_agreement"] = f"{stop_agree}/{rows}"
        result["stop_judgment_disagreements"] = sorted(stop_differ)
        result["reference_issue_questions"] = {value: sorted(ids) for value, ids in reference.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

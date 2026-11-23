"""Unblind two reviewers' sheets and print counts only (safe to commit).

Run after both sheets are complete. Reads key.json from the pack directory.
The output holds, per method and column, how many rows took each value, how
often the two reviewers agree, and the question ids where they differ. It
holds no question, answer or remark text. When both sheets carry the PDF
form's two extra judgements in the remark column, stop judgements and
reference-answer flags are counted too. Free-text remarks are checked for presence only.

A sheet that breaks the filling rules is listed row by row and nothing is scored:
it goes back to its reviewer. Use --check on a single sheet when it comes in.
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
    reviewers = {(r.get("reviewer") or "").strip().casefold() for r in rows}
    if len(reviewers) != 1 or not next(iter(reviewers)):
        raise ValueError("SHEET_REQUIRES_ONE_REVIEWER: " + str(path))
    for row in rows:
        for column, allowed in CHOICES.items():
            if row[column].strip() not in allowed:
                raise ValueError(f"INVALID_VALUE: {path} {row['question_id']}{row['label']} {column}")
    result = {}
    for row in rows:
        remark = row.get("remark") or ""
        has_remark = any(part.strip() and part.partition("=")[0].strip() not in {label for label, _ in EXTRA.values()}
                         for part in remark.split("；"))
        values = {**{c: row[c].strip() for c in CHOICES}, **extras(remark),
                  "reviewer": next(iter(reviewers)), "has_remark": has_remark}
        for column, allowed in EXTRA_CHOICES.items():  # a real column wins over the remark
            if (row.get(column) or "").strip():
                if row[column].strip() not in allowed:
                    raise ValueError(f"INVALID_VALUE: {path} {row['question_id']}{row['label']} {column}")
                values[column] = row[column].strip()
        result[(row["question_id"], row["label"])] = values
    return result


def row_problems(sheet, rows):
    """Every place where a returned sheet breaks the filling rules, as "question+label column: reason".

    A sheet with problems goes back to its reviewer. Nobody else changes a rating, so the
    counts never rest on values that were filled in or corrected for the reviewer.
    """
    problems = []
    for (question_id, label), row in sorted(sheet.items()):
        facts, where = rows[question_id][label], f"{question_id}{label}"
        stopped = facts["outcome"] == "stop"
        for column in ("conclusions_correct", "missing_points", "citations_support"):
            if (row[column] == "不适用") != stopped:
                problems.append(f"{where} {column}: " + ("停止的输出应填“不适用”" if stopped else "答复的输出不能填“不适用”"))
        if (row["excerpt_relevant"] == "无摘录") == facts["has_excerpt"]:
            problems.append(f"{where} excerpt_relevant: " + ("该输出有“原文摘录：”条目，应填是或否" if facts["has_excerpt"]
                                                             else "该输出没有“原文摘录：”条目，应填“无摘录”"))
        if "stop_judgment" not in row or "reference_issue" not in row:
            problems.append(f"{where} stop_judgment/reference_issue: 未填写")
        elif (row["stop_judgment"] == "不适用") == stopped:
            problems.append(f"{where} stop_judgment: " + ("停止的输出应判断合理、不合理或待确认" if stopped
                                                           else "答复的输出应填“不适用”"))
    for question_id in sorted({q for q, _ in sheet}):
        flags = {row.get("reference_issue") for (q, _), row in sheet.items() if q == question_id}
        if len(flags) > 1:
            problems.append(f"{question_id} reference_issue: 同一题的各份输出应填相同的值")
        if "有误" in flags and not any(row["has_remark"] for (q, _), row in sheet.items() if q == question_id):
            problems.append(f"{question_id} reference_issue: 标为有误时应在备注写明理由")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--sheets", type=Path, nargs="+", required=True)
    parser.add_argument("--check", action="store_true",
                        help="Only check the given sheet(s) against the filling rules; the key is not used for scoring")
    parser.add_argument("--output", type=Path, help="New JSON file under evidence/ (not used with --check)")
    args = parser.parse_args()
    key = json.loads((args.pack / "key.json").read_text("utf-8"))
    rows_path = args.pack / "rows.json"
    if not rows_path.is_file():
        parser.error("REVIEW_ROW_RULES_REQUIRED")
    rows = json.loads(rows_path.read_text("utf-8"))
    if set(rows) != set(key) or any(set(rows[q]) != set(key[q]) for q in key):
        parser.error("REVIEW_ROW_RULES_DO_NOT_MATCH_KEY")
    try:
        sheets = [read_sheet(path, key) for path in args.sheets]
    except ValueError as error:
        parser.error(str(error))
    if len({next(iter(sheet.values()))["reviewer"] for sheet in sheets}) != len(sheets):
        parser.error("DISTINCT_REVIEWERS_REQUIRED")
    problems = {str(path): row_problems(sheet, rows) for path, sheet in zip(args.sheets, sheets)}
    if any(problems.values()) or args.check:
        # Count-free: reviewers get this list back and correct their own sheet.
        print(json.dumps({"checked": [str(path) for path in args.sheets], "rules_available": rows is not None,
                          "problems": problems}, ensure_ascii=False, indent=1))
        sys.exit(1 if any(problems.values()) else 0)
    if len(sheets) != 2 or args.output is None:
        parser.error("TWO_SHEETS_AND_OUTPUT_REQUIRED_FOR_SCORING")
    if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
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
    rows_checked = rows is not None  # False for packs built before rows.json existed
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
    result = {"scope": "counts and question ids only", "sheets_passed_rule_check_unchanged": rows_checked,
              "questions": len(key), "rows_per_sheet": rows,
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

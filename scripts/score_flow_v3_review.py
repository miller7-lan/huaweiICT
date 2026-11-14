"""Unblind two reviewers' sheets and print counts only (safe to commit).

Run after both sheets are complete. Reads key.json from the pack directory.
The output holds, per method and column, how many rows took each value, how
often the two reviewers agree, and the question ids where they differ. It
holds no question, answer or remark text.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_flow_v3_review_pack import CHOICES  # noqa: E402


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
    return {(r["question_id"], r["label"]): {c: r[c].strip() for c in CHOICES} for r in rows}


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
    by_method = {"single_shot": {c: Counter() for c in CHOICES}, "flow_v3": {c: Counter() for c in CHOICES}}
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
    rows = len(key) * 2
    result = {"scope": "counts and question ids only", "questions": len(key), "rows_per_sheet": rows,
              "ratings_per_method_and_column": len(key) * 2,
              "by_method": {m: {c: dict(sorted(n.items())) for c, n in cols.items()} for m, cols in by_method.items()},
              "reviewer_agreement": {c: f"{agree[c]}/{rows}" for c in CHOICES},
              "disagreements": {c: sorted(v) for c, v in differ.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

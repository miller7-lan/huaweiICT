"""Unblind returned review sheets and count per-method results for the holdout run.

Reads one or more filled score sheets (CSV from build_holdout_review_package.py)
and the private A/B key of the run. Invalid or empty cells are reported, never
guessed. Rows rated by two reviewers also give agreement counts. Only counts are
written to the repository; the sheets and key stay where they are.
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime/frozen-qa-20261030/eval"
EVIDENCE = ROOT / "evidence/frozen-qa-20261030"
FIELDS = {"结果可接受(是/否)": {"是", "否"},
          "关键断言覆盖(全部/部分/无/不适用)": {"全部", "部分", "无", "不适用"},
          "有事实错误(是/否)": {"是", "否"},
          "引用支撑结论(是/否/不适用)": {"是", "否", "不适用"},
          "触犯禁止项(是/否)": {"是", "否"},
          "总评(2/1/0)": {"2", "1", "0"}}


def read_sheet(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def score(sheets, key):
    problems, ratings = [], {}
    for path in sheets:
        for number, row in enumerate(read_sheet(path), 2):
            reviewer = (row.get("评审人") or "").strip()
            bad = [f for f, allowed in FIELDS.items() if (row.get(f) or "").strip() not in allowed]
            if not reviewer or bad or row["题号"] not in key or row["输出"] not in ("A", "B"):
                problems.append({"sheet": Path(path).name, "line": number, "fields": bad or ["评审人/题号/输出"]})
                continue
            ratings.setdefault((row["题号"], row["输出"]), {})[reviewer] = {f: row[f].strip() for f in FIELDS} \
                | {"类别": row["类别"], "应有结果": row["应有结果"], "实际结果": row["实际结果"], "评审人": reviewer}
    methods = sorted({m for pair in key.values() for m in pair.values()})
    result, by_expected, by_reviewer_stats, outcomes = {}, {}, {}, {}
    for method in methods:
        rows = [(qid, label, by_reviewer) for (qid, label), by_reviewer in ratings.items()
                if key[qid][label] == method]
        flat = [r for _, _, by_reviewer in rows for r in by_reviewer.values()]
        result[method] = counts(flat) | {"outputs_rated": len(rows)}
        by_expected[method] = {e: counts([r for r in flat if r["应有结果"] == e]) for e in ("答复", "停止")}
        by_reviewer_stats[method] = {name: counts([r for r in flat if r["评审人"] == name])
                                     for name in sorted({r["评审人"] for r in flat})}
        # Answer/stop counts come from the pre-filled sheet columns, not from any rating.
        first = [next(iter(by_reviewer.values())) for _, _, by_reviewer in rows]
        outcomes[method] = {f"expected_{e}_actual_{a}": sum(r["应有结果"] == e and r["实际结果"] == a for r in first)
                            for e in ("答复", "停止") for a in ("答复", "停止")}
    doubled = {pair: list(by_reviewer.values()) for pair, by_reviewer in ratings.items() if len(by_reviewer) == 2}
    agreement = {"double_rated_outputs": len(doubled),
                 "same_outcome_acceptable": sum(a["结果可接受(是/否)"] == b["结果可接受(是/否)"] for a, b in doubled.values()),
                 "same_overall": sum(a["总评(2/1/0)"] == b["总评(2/1/0)"] for a, b in doubled.values()),
                 "same_by_field": {f: sum(a[f] == b[f] for a, b in doubled.values()) for f in FIELDS},
                 # Question and blind label only, for the later joint discussion; no text.
                 "overall_disagreements": sorted(f"{qid}{label}:{key[qid][label]}" for (qid, label), (a, b)
                                                 in doubled.items() if a["总评(2/1/0)"] != b["总评(2/1/0)"])}
    return {"by_method": result, "by_method_expected": by_expected, "by_method_reviewer": by_reviewer_stats,
            "outcome_counts": outcomes, "agreement": agreement, "invalid_rows": problems,
            "reviewers": sorted({name for by_reviewer in ratings.values() for name in by_reviewer})}


def counts(flat):
    count = lambda field, value: sum(r[field] == value for r in flat)
    return {"ratings": len(flat),
            "outcome_acceptable": count("结果可接受(是/否)", "是"),
            "coverage": {v: count("关键断言覆盖(全部/部分/无/不适用)", v) for v in ("全部", "部分", "无", "不适用")},
            "factual_errors": count("有事实错误(是/否)", "是"),
            "unsupported_citations": count("引用支撑结论(是/否/不适用)", "否"),
            "must_not_violations": count("触犯禁止项(是/否)", "是"),
            "overall": {v: count("总评(2/1/0)", v) for v in ("2", "1", "0")},
            "overall_mean": round(sum(int(r["总评(2/1/0)"]) for r in flat) / len(flat), 3) if flat else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("sheets", nargs="+", type=Path)
    args = parser.parse_args()
    key = json.loads((RUNTIME / args.run_id / "key.json").read_text("utf-8"))
    summary = {"run_id": args.run_id, **score(args.sheets, key)}
    # Reviewer names are kept as entered; the summary holds counts only, no question or answer text.
    (EVIDENCE / f"holdout-review-{args.run_id}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

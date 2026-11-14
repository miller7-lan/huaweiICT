"""Build a blind two-reviewer pack from a paired-run records.jsonl (private, never committed).

Reads the records written by compare_frozen_flow_v3.py and the corpus they used.
For every question it shows the question, outputs A and B in a per-question seeded
random order, and the full text of every unit the outputs cite. Method names,
result kinds and stop origins are not shown. Wording inside an output can still
hint at the method (for example a partial-answer note or an excerpt marker); the
reviewers are told so and should not guess.

Writes under a new directory in .runtime: review.md (reading material), one empty
sheet per reviewer (sheet-<name>.csv) and key.json (label -> method, keep it away
from the reviewers until both sheets are in). Questions, answers and source text
are in these files; do not put them in Git.
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

COLUMNS = ["question_id", "label", "reviewer", "conclusions_correct", "missing_points",
           "citations_support", "excerpt_relevant", "remark"]
CHOICES = {"conclusions_correct": ("是", "否", "部分", "不适用"),
           "missing_points": ("否", "漏次要", "漏核心", "不适用"),
           "citations_support": ("是", "否", "不适用"),
           "excerpt_relevant": ("是", "否", "无摘录")}
RULES = """# 评审说明

每份输出一行，在你自己的表里填四列取值（备注列自己留，不上传）。**独立评，不与另一位评审讨论，评完前不要看 key.json。**
输出里偶尔会有提示它来自哪种方法的措辞（如“部分答复”“原文摘录”）。不要据此猜方法，也不要因此给分。

| 列 | 取值 | 怎么填 |
| --- | --- | --- |
| conclusions_correct 保留的结论是否正确 | 是 / 否 / 部分 / 不适用 | 对照下方原文和你的参考答案。输出是停止时填“不适用”；该停止的题，停止是否合理写在备注里 |
| missing_points 是否漏掉必答要点 | 否 / 漏次要 / 漏核心 / 不适用 | 对照参考答案的必答要点。漏核心 = 问题要问的主要内容没答到。停止时填“不适用” |
| citations_support 引文是否支持结论中的条件和限定语 | 是 / 否 / 不适用 | 重点看“有条件”“仅限”“除外”这类限定语有没有被引文覆盖。停止时填“不适用” |
| excerpt_relevant 带“原文摘录”的条目是否与问题相关 | 是 / 否 / 无摘录 | 没有该标记的输出填“无摘录” |

另外在备注里记：这道题按参考答案应当答复还是应当停止；你认为参考答案本身有误时写“参考有误”。
"""


def read_records(path):
    rows = [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line.strip()]
    by_question = {}
    for record in rows:
        by_question.setdefault(record["question_id"], {})[record["compared_as"]] = record
    for question_id, pair in by_question.items():
        if set(pair) != {"single_shot", "flow_v3"}:
            raise ValueError("INCOMPLETE_PAIR: " + question_id)
    return by_question


def cited_units(final, corpus):
    seen, result = set(), []
    for item in final["assertions"]:
        key = (item["source_id"], item["locator"])
        if key not in seen and key in corpus:
            seen.add(key)
            result.append(corpus[key])
    return result


def render_output(label, final, corpus):
    lines = [f"#### 输出 {label}", "", f"**结果**：{'答复' if final['outcome'] == 'answer' else '停止'}", ""]
    if final["outcome"] == "answer":
        lines += ["**答复**", "", final["answer"], "", "**逐条结论与引文**", ""]
        for index, item in enumerate(final["assertions"], 1):
            lines += [f"{index}. {item['claim']}", f"   - 引用：{item['source_id']} · {item['locator']}",
                      f"   - 引文：{item['quote']}"]
    else:
        lines += [f"**停止说明**：{final['answer']}", f"**停止理由**：{final['stop_reason']}"]
    if final["unknowns"]:
        lines += ["", "**未能确定的事项**", ""] + [f"- {text}" for text in final["unknowns"]]
    units = cited_units(final, corpus)
    if units:
        lines += ["", f"**输出 {label} 引用的原文单元（全文）**", ""]
        lines += [f"- {u['source_id']} · {u['locator']}：{u['text']}" for u in units]
    return lines + [""]


def build(records, corpus, output, seed, reviewers):
    output.mkdir(parents=True)
    key, parts = {}, ["# 评审材料（私有，含题目与原文，不入 Git）", "", RULES]
    for question_id in sorted(records):
        pair = records[question_id]
        labels = ["single_shot", "flow_v3"]
        random.Random(f"{seed}:{question_id}:blind").shuffle(labels)
        key[question_id] = {"A": labels[0], "B": labels[1]}
        parts += [f"## {question_id}", "", f"**问题**：{pair[labels[0]]['question']}", ""]
        for label, method in zip("AB", labels):
            parts += render_output(label, pair[method]["final"], corpus)
    (output / "review.md").write_text("\n".join(parts) + "\n", "utf-8")
    (output / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), "utf-8")
    for name in reviewers:
        with (output / f"sheet-{name}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for question_id in sorted(records):
                for label in "AB":
                    writer.writerow([question_id, label, name, "", "", "", "", ""])
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory inside .runtime")
    parser.add_argument("--seed", default="20261114-review")
    parser.add_argument("--reviewers", nargs=2, required=True, metavar=("NAME1", "NAME2"),
                        help="Ascii names used in file names, for example reviewer1 reviewer2")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()) or args.output.exists():
        parser.error("NEW_DIRECTORY_UNDER_RUNTIME_REQUIRED")
    corpus = {(u["source_id"], u["locator"]): u for u in json.loads(args.corpus.read_text("utf-8"))["units"]}
    key = build(read_records(args.records), corpus, args.output, args.seed, args.reviewers)
    print(json.dumps({"output": str(args.output), "questions": len(key), "rows_per_sheet": len(key) * 2},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

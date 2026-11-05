"""Assemble the human blind-review package for the frozen 30-question holdout run.

Inputs are the author folder (questions, reference, freeze) and the isolated
evaluation folder (blind file, run receipt); every hash is cross-checked first.
The package holds side-by-side review material, a CSV score sheet with one row
per blind output and the review rules. The A/B key never enters it. Output stays
local, outside git: it contains the reference answers.
"""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["题号", "类别", "应有结果", "可接受结果", "输出", "实际结果", "结果可接受(是/否)",
           "关键断言覆盖(全部/部分/无/不适用)", "有事实错误(是/否)", "引用支撑结论(是/否/不适用)",
           "触犯禁止项(是/否)", "总评(2/1/0)", "评审人", "备注"]
RULES = """# 30题人工盲评说明

每道题有两份系统输出，标为 A 和 B。A/B 对应哪种方法已按题随机打乱，密钥由队长保管，评完才揭晓。请不要猜测或讨论哪份是哪种方法。

## 材料

- `评审材料-{run_id}.md`：逐题列出问题、参考关键断言、不得出现的内容、停止边界，以及输出 A、B。
- `评分表-{run_id}.csv`：每份输出一行，共 {rows} 行，前六列已填好，只填后面各列。用 WPS 或 Excel 打开，保存时仍选 CSV（UTF-8）。
- `sources/`：7份冻结资料原文（按定位分节的 Markdown），核对引文用；需要时也可按[评测范围](https://github.com/miller7-lan/huaweiICT/blob/main/evaluation/independent-20261030/scope.md)里的 URL 查看原件。
- `questions.json`、`reference.json`、`blind-{run_id}.json`：原始文件，有疑问时对照。

## 每列怎么填

| 列 | 填法 |
| --- | --- |
| 结果可接受 | 实际结果（答复/停止）在"可接受结果"里填"是"，否则"否" |
| 关键断言覆盖 | 只看答复题：参考关键断言全部讲到填"全部"，讲到一部分填"部分"，都没讲到填"无"；停止题或输出为停止时填"不适用" |
| 有事实错误 | 输出里有与原始资料不符的说法（数字、日期、条件、机构、文件名等）填"是"，并在备注写明哪句 |
| 引用支撑结论 | 输出给出的引文确实来自所写来源和定位、并能支撑结论填"是"；引文对不上或撑不起结论填"否"；没有引文的停止输出填"不适用" |
| 触犯禁止项 | 输出出现了"不得出现"所列内容填"是"，并在备注写明 |
| 总评 | 2 = 可直接给办事人员参考；1 = 方向对但需要改；0 = 不能用或可能误导 |
| 评审人 | 填自己的名字 |
| 备注 | 判"否""是（错误/禁止项）"或总评 0、1 时必填一句理由 |

停止题的好输出是：按停止边界停下，说清缺什么依据，不顺带给出可拿来办事的数字或结论。答复题的好输出是：讲到关键断言、引文可核、没有额外编造。资料都是冻结的历史归档，任何输出把它说成"现行有效"都算事实错误。

## 交回

评完把评分表改名为 `评分表-{run_id}-<姓名>.csv` 发回队长。评分时独立完成，交回前不和其他评审人对答案。
"""


OUTCOMES = {"answer": "答复", "stop": "停止"}


def zh(values):
    return [OUTCOMES.get(value, value) for value in as_list(values)]


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def check(author, evaluation):
    freeze = load(author / "output/freeze.json")
    blinds = sorted((evaluation / "output").glob("blind-*.json"))
    if len(blinds) != 1:
        raise SystemExit("EXACTLY_ONE_BLIND_FILE_REQUIRED")
    receipt = load(evaluation / "output/run-receipt.json")
    checks = {"questions": digest(author / "output/questions.json") == freeze["questions_sha256"]
              == receipt["questions_sha256"],
              "reference": digest(author / "output/reference.json") == freeze["reference_sha256"],
              "eval_questions": digest(evaluation / "questions.json") == freeze["questions_sha256"],
              "blind": digest(blinds[0]) == receipt["blind_sha256"],
              "run_id": blinds[0].name == f"blind-{receipt['run_id']}.json"}
    if not all(checks.values()):
        raise SystemExit("HASH_MISMATCH: " + ", ".join(k for k, ok in checks.items() if not ok))
    questions = load(author / "output/questions.json")
    reference = {r["id"]: r for r in load(author / "output/reference.json")}
    blind = load(blinds[0])
    by_id = {item["id"]: item for item in blind["items"]}
    if set(by_id) != {q["id"] for q in questions} or set(reference) != set(by_id) \
            or any(by_id[q["id"]]["question"] != q["question"] for q in questions):
        raise SystemExit("BLIND_FILE_DOES_NOT_MATCH_QUESTIONS")
    return questions, reference, blind, blinds[0]


def render_output(label, output):
    lines = [f"### 输出 {label}（{'答复' if output['outcome'] == 'answer' else '停止'}）", "", output["answer"] or "（无）", ""]
    for a in output["assertions"]:
        lines.append(f"- {a.get('claim', '')}  ")
        lines.append(f"  「{a.get('quote', '')}」—— {a.get('source_id', '')} {a.get('locator', '')}")
    if output["stop_reason"]:
        lines += ["", f"停止理由：{output['stop_reason']}"]
    if output["unknowns"]:
        lines += ["", "未知项：" + "；".join(output["unknowns"])]
    return lines + [""]


def material(questions, reference, blind, sources):
    run_id = blind["run_id"]
    lines = [f"# 30题盲评材料（{run_id}）", "", "A/B 已按题随机打乱。参考内容只用于评审，不要转发给他人。", "",
             "参考答案用资料编号，系统输出用来源 ID，对应如下；原文在 `sources/` 目录。", "",
             "| 编号 | 来源 ID | 原文文件 |", "| --- | --- | --- |",
             *[f"| {name.split('-', 1)[0]} | {name.split('-', 1)[1][:-3]} | sources/{name} |" for name in sources], ""]
    by_id = {item["id"]: item for item in blind["items"]}
    for q in questions:
        r, item = reference[q["id"]], by_id[q["id"]]
        lines += [f"## {q['id']} · {r['category']}", "", f"**问题**：{q['question']}", ""]
        if q.get("context"):
            lines += [f"**情境**：{q['context']}", ""]
        lines += [f"**应有结果**：{zh(r['expected_outcome'])[0]}（可接受：{'、'.join(zh(r.get('acceptable_outcomes')))}）", ""]
        if r.get("key_assertions"):
            lines += ["**参考关键断言**", ""]
            for number, a in enumerate(r["key_assertions"], 1):
                lines += [f"{number}. {a.get('claim', '')} —— {a.get('source', '')} {a.get('locator', '')}",
                          f"   > {a.get('quote', '')}"]
            lines.append("")
        for title, field in (("不得出现", "must_not"), ("停止边界", "stop_boundary"), ("出题备注", "notes")):
            values = as_list(r.get(field))
            if values:
                lines += [f"**{title}**：", *[f"- {v}" for v in values], ""]
        lines += render_output("A", item["A"]) + render_output("B", item["B"]) + ["---", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author-dir", type=Path, required=True)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Default: <Desktop>/ict-holdout-review-<run_id>")
    args = parser.parse_args()
    questions, reference, blind, blind_path = check(args.author_dir, args.eval_dir)
    run_id = blind["run_id"]
    out = args.out or ROOT.parent / f"ict-holdout-review-{run_id}"
    if out.exists():
        raise SystemExit("OUTPUT_EXISTS_NOT_OVERWRITTEN: " + str(out))
    out.mkdir(parents=True)
    shutil.copy2(args.author_dir / "output/questions.json", out / "questions.json")
    shutil.copy2(args.author_dir / "output/reference.json", out / "reference.json")
    shutil.copy2(blind_path, out / f"blind-{run_id}.json")
    shutil.copytree(args.author_dir / "sources", out / "sources")
    shutil.copy2(args.author_dir / "sources-SHA256.txt", out / "sources" / "SHA256SUMS.txt")
    sources = sorted(p.name for p in (out / "sources").glob("*.md"))
    (out / f"评审材料-{run_id}.md").write_text(material(questions, reference, blind, sources), "utf-8")
    rows = 0
    with (out / f"评分表-{run_id}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        by_id = {item["id"]: item for item in blind["items"]}
        for q in questions:
            r = reference[q["id"]]
            for label in "AB":
                outcome = by_id[q["id"]][label]["outcome"]
                writer.writerow([q["id"], r["category"], zh(r["expected_outcome"])[0],
                                 "/".join(zh(r.get("acceptable_outcomes"))), label, zh(outcome)[0]]
                                + [""] * (len(COLUMNS) - 6))
                rows += 1
    (out / "评审说明.md").write_text(RULES.format(run_id=run_id, rows=rows), "utf-8")
    files = sorted(p for p in out.iterdir() if p.is_file())
    (out / "SHA256SUMS.txt").write_text("".join(f"{digest(p)} *{p.name}\n" for p in files), "utf-8")
    archive = shutil.make_archive(str(out), "zip", out.parent, out.name)
    print(json.dumps({"run_id": run_id, "folder": str(out), "zip": archive, "zip_sha256": digest(archive),
                      "questions": len(questions), "score_rows": rows, "key_included": False},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

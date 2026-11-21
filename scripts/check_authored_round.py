"""Mechanical intake of an independently written question round (no network, no model).

from-table  turn the writer's filled table (CSV, UTF-8) into questions.json and reference.json.
check       check questions.json, reference.json and freeze.json against the authoring brief
            (evaluation/crossdoc-flow-v3/independent-A1/给出题人的说明.md) and print counts only.

check never prints question text, key points or quotes, so its output can be shown to anyone.
It does not judge whether a question is good or a reference is right: that is the human
review (A2). Optional --corpus checks that every quote appears verbatim in the frozen corpus;
optional --prior screens topic overlap against earlier question files by character bigrams.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gov_mcp import frozen_qa  # noqa: E402
from compare_frozen_flow_v3 import check_reference  # noqa: E402

COUNTS = {"comparison": 8, "temporal": 4, "inference": 4, "should_stop": 8}
STOP_REASONS = {"insufficient": 2, "conflict": 2, "branch": 2, "out_of_scope": 2}
MAX_QUESTION_CHARS = 80
# Only out-of-scope questions may carry these; the other stops must be judged from the evidence.
GIVEAWAY = re.compile(r"现在|目前|今天|今年|最新|现行|还有效|仍有效|有效吗|身份证号|\d{15,18}|[A-Za-z]?\d{6}(?:19|20)\d{2}")
OTHER_PROVINCES = ("北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
                   "福建", "江西", "山东", "湖北", "湖南", "广东", "广西", "海南", "四川", "贵州", "云南", "西藏",
                   "陕西", "甘肃", "青海", "宁夏", "新疆", "内蒙古", "香港", "澳门", "台湾", "南京", "杭州", "广州",
                   "深圳", "成都", "武汉", "西安", "长沙", "合肥", "济南", "石家庄", "太原")
MATERIAL = re.compile(r"资料\s*([1-7])")
TABLE_COLUMNS = ("id", "category", "expected_outcome", "stop_reason", "question", "key_points", "sources",
                 "quotes", "stop_basis", "must_not")
SPLIT = re.compile(r"[｜|\n]+")


def split_cell(value):
    return [part.strip() for part in SPLIT.split(value or "") if part.strip()]


def from_table(table, questions_path, reference_path):
    with open(table, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or set(TABLE_COLUMNS) - set(rows[0]):
        raise ValueError("TABLE_COLUMNS_REQUIRED: " + ", ".join(TABLE_COLUMNS))
    questions, reference = [], []
    for row in rows:
        if not (row["id"] or "").strip():
            continue
        item_id = row["id"].strip()
        questions.append({"id": item_id, "question": row["question"].strip()})
        entry = {"id": item_id, "expected_outcome": row["expected_outcome"].strip(),
                 "category": row["category"].strip(), "key_points": split_cell(row["key_points"]),
                 "sources": split_cell(row["sources"]), "quotes": split_cell(row["quotes"])}
        if row["stop_reason"].strip():
            entry["stop_reason"] = row["stop_reason"].strip()
        if row["stop_basis"].strip():
            entry["stop_basis"] = row["stop_basis"].strip()
        if split_cell(row["must_not"]):
            entry["must_not"] = split_cell(row["must_not"])
        reference.append(entry)
    for path, data in ((questions_path, questions), (reference_path, reference)):
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    return len(questions)


def bigrams(text):
    text = re.sub(r"\s+|只读归档[:：]?", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    a, b = bigrams(a), bigrams(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def prior_questions(paths):
    found = []
    for path in paths:
        data = json.loads(Path(path).read_text("utf-8"))
        items = data.get("cases", data.get("questions", [])) if isinstance(data, dict) else data
        found += [item["question"] for item in items if isinstance(item, dict) and item.get("question")]
    return found


def normalise(text):
    return re.sub(r"\s+", "", text)


def check(questions_path, reference_path, freeze_path=None, corpus=None, prior=(), excel_material=None):
    errors, warnings = [], []
    questions = json.loads(Path(questions_path).read_text("utf-8"))
    try:
        frozen_qa.validate_questions(questions)
        reference_summary = check_reference(reference_path, questions)
    except ValueError as error:
        return {"errors": [str(error)], "warnings": [], "passed": False}
    reference = {item["id"]: item for item in json.loads(Path(reference_path).read_text("utf-8"))}
    ids = [item["id"] for item in questions]
    if ids != [f"X{n:02d}" for n in range(1, len(ids) + 1)]:
        errors.append("IDS_NOT_CONTIGUOUS_FROM_X01")
    categories = Counter(reference[i]["category"] for i in ids)
    if dict(categories) != COUNTS:
        errors.append("CATEGORY_COUNTS " + json.dumps(dict(categories), ensure_ascii=False))
    reasons = Counter(reference[i].get("stop_reason") for i in ids if reference[i]["category"] == "should_stop")
    if dict(reasons) != STOP_REASONS:
        errors.append("STOP_REASON_COUNTS " + json.dumps(dict(reasons), ensure_ascii=False))
    units = json.loads(Path(corpus).read_text("utf-8"))["units"] if corpus else None
    corpus_text = [normalise(unit["text"]) for unit in units] if units else None
    quotes_checked = quotes_missing = 0
    excel_questions = 0
    for item in questions:
        item_id, text, ref = item["id"], item["question"], reference[item["id"]]
        stop = ref["category"] == "should_stop"
        if (ref["expected_outcome"] == "stop") != stop:
            errors.append("OUTCOME_CATEGORY_MISMATCH " + item_id)
        if len(text) > MAX_QUESTION_CHARS:
            errors.append("QUESTION_TOO_LONG " + item_id)
        if ref.get("stop_reason") != "out_of_scope" and (GIVEAWAY.search(text)
                                                          or any(name in text for name in OTHER_PROVINCES)):
            errors.append("GIVEAWAY_WORDING " + item_id)
        if stop:
            if not (ref.get("stop_basis") or "").strip():
                errors.append("STOP_BASIS_REQUIRED " + item_id)
            if ref.get("stop_reason") in ("insufficient", "branch", "conflict") and not ref.get("sources"):
                warnings.append("STOP_WITHOUT_SOURCES " + item_id)
            continue
        sources, quotes = ref.get("sources", []), ref.get("quotes", [])
        materials = {match for source in sources for match in MATERIAL.findall(source)}
        if not sources or (ref["category"] != "inference" and len(materials) < 2):
            errors.append("NEEDS_TWO_MATERIALS " + item_id)
        if len(quotes) < len(ref["key_points"]):
            errors.append("QUOTE_PER_KEY_POINT_REQUIRED " + item_id)
        if (str(excel_material) in materials if excel_material
                else any("Excel" in s or "表格" in s or "收费目录" in s for s in sources)):
            excel_questions += 1
        if corpus_text is not None:
            for quote in quotes:
                quotes_checked += 1
                if not any(normalise(quote) in unit for unit in corpus_text):
                    quotes_missing += 1
                    warnings.append("QUOTE_NOT_FOUND_IN_CORPUS " + item_id)
    coverage = Counter(match for ref in reference.values() for source in ref.get("sources", [])
                       for match in set(MATERIAL.findall(source)))
    thin = sorted(m for m in "1234567" if coverage[m] < 2)
    if thin:
        warnings.append("MATERIALS_IN_FEWER_THAN_TWO_QUESTIONS 资料" + ",".join(thin))
    if excel_questions < 3:
        errors.append(f"EXCEL_QUESTIONS {excel_questions} < 3")
    overlap = None
    if prior:
        earlier = prior_questions(prior)
        scores = {item["id"]: max((similarity(item["question"], old) for old in earlier), default=0.0)
                  for item in questions}
        overlap = {"prior_questions": len(earlier), "max_similarity": round(max(scores.values()), 3),
                   "above_0_5": sorted(i for i, s in scores.items() if s > 0.5)}
        if overlap["above_0_5"]:
            warnings.append("TOPIC_OVERLAP_ABOVE_0_5 " + ",".join(overlap["above_0_5"]))
    freeze = None
    if freeze_path:
        freeze = json.loads(Path(freeze_path).read_text("utf-8"))
        for field in ("files_read", "ai_tools_used", "declaration"):
            if field not in freeze:
                errors.append("FREEZE_FIELD_REQUIRED " + field)
    else:
        errors.append("FREEZE_FILE_REQUIRED")
    return {"questions": len(questions),
            "questions_sha256": sha256(Path(questions_path).read_bytes()).hexdigest(),
            "reference_sha256": reference_summary["reference_sha256"],
            "freeze_sha256": sha256(Path(freeze_path).read_bytes()).hexdigest() if freeze_path else None,
            "categories": dict(categories), "stop_reasons": dict(reasons),
            "excel_questions": excel_questions, "material_coverage": {m: coverage[m] for m in "1234567"},
            "quotes": None if corpus_text is None else {"checked": quotes_checked, "not_found": quotes_missing},
            "overlap": overlap, "ai_tools_declared": None if freeze is None else bool(freeze.get("ai_tools_used")),
            "errors": errors, "warnings": warnings, "passed": not errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    table = commands.add_parser("from-table")
    table.add_argument("--table", type=Path, required=True)
    table.add_argument("--questions", type=Path, required=True)
    table.add_argument("--reference", type=Path, required=True)
    checking = commands.add_parser("check")
    checking.add_argument("--questions", type=Path, required=True)
    checking.add_argument("--reference", type=Path, required=True)
    checking.add_argument("--freeze", type=Path)
    checking.add_argument("--corpus", type=Path, help="593-unit corpus.json, to check quotes verbatim")
    checking.add_argument("--prior", type=Path, nargs="*", default=(), help="Earlier question files (private)")
    checking.add_argument("--excel-material", type=int, choices=range(1, 8),
                          help="Number of the Excel fee catalogue in 资料清单.txt")
    checking.add_argument("--output", type=Path, help="Write the counts-only result here")
    args = parser.parse_args()
    if args.command == "from-table":
        print(json.dumps({"questions": from_table(args.table, args.questions, args.reference)}))
        return 0
    result = check(args.questions, args.reference, args.freeze, args.corpus, args.prior, args.excel_material)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", "utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

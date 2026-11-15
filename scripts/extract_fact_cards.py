"""Rule-based fact cards from the frozen corpus: amounts, durations, dates, years, document
numbers, subjects, qualifiers and table cells, each pointing back to its exact text.

No model call and no network. Every card records its source, locator, character span and the
exact text at that span, so it can be checked against the unit at any time. The cards are a
candidate layer: nothing in the answer flow reads them yet.

  extract  corpus.json -> private cards.jsonl (holds source text) and a count-only summary
           that can be committed
  sample   draw a stratified sample of cards into a private CSV for a person to mark correct
  score    read the marked CSV and write a count-only precision summary

A card is "correct" when its type, value and subjects are what a careful reader takes from
the sentence. Only a person fills that column.
"""
import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp import frozen_qa  # noqa: E402
from gov_mcp.frozen_qa_flow_v3 import DOCUMENT_NUMBER, _document_number_key, table_columns  # noqa: E402

VERSION = "fact-cards-v1"
TYPES = ("amount", "duration", "count", "date", "year", "document_number", "table_cell")

NUMERAL = r"\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+"
AMOUNT = re.compile(rf"(?P<number>{NUMERAL})\s*(?P<unit>万元|元)")
DURATION = re.compile(rf"(?P<number>{NUMERAL})\s*(?P<unit>个工作日|工作日|个自然日|自然日|个月|周|小时|天|日|年)")
COUNT = re.compile(rf"(?P<number>{NUMERAL})\s*(?P<unit>次|份|张|项|种|岁)")
FULL_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{2}-\d{2}")
YEAR = re.compile(r"(?<![\d〔\[［(（])\d{4}\s*年(?!\s*\d{1,2}\s*月)")
# Business branches and objects named in the corpus; longer names first so they win.
SUBJECTS = ("临时居民身份证", "临时身份证", "临时证", "首次申领", "到期换领", "损坏换领", "遗失补领", "换领", "补领",
            "挂失", "异地", "省内通办", "跨省通办", "代领", "代办", "邮寄", "考生", "未成年人", "居民身份证")
QUALIFIERS = ("不得", "禁止", "不予", "不能", "仅限", "只限", "必须", "应当", "可以", "有条件", "除外", "免收", "停征",
              "无需", "不需", "减半")
SENTENCE = re.compile(r"[^。；;！？\n]+[。；;！？\n]?")
CLAUSE = re.compile(r"[^，,。；;！？\n]+[，,。；;！？\n]?")
# Words the document-number pattern may swallow in front of an issuer abbreviation.
LEAD = re.compile(r"^(?:依据|根据|按照|参照|执行|印发|按|见|即|和|及|与|的)+")


def unit_number(text):
    text = text.strip()
    return text if re.fullmatch(r"\d+(?:\.\d+)?", text) else text  # Chinese numerals kept as written


def sentences(text):
    return [(m.start(), m.end()) for m in SENTENCE.finditer(text) if m.group().strip()]


def around(text, spans, start, end):
    for left, right in spans:
        if left <= start and end <= right:
            return left, right
    return 0, len(text)


def mentions(sentence, words):
    found, taken = [], []
    for word in words:  # longest first: 到期换领 before 换领
        for match in re.finditer(re.escape(word), sentence):
            if not any(a <= match.start() < b for a, b in taken):
                taken.append((match.start(), match.end()))
                found.append(word)
    return sorted(set(found), key=words.index)


def date_role(text, start, end, unit_kind):
    if unit_kind in ("page_publication_date", "page_modified_date"):
        return unit_kind
    for match in frozen_qa.TYPED_DATE.finditer(text):
        if match.start() <= start and end <= match.end():
            return frozen_qa.typed_date(match)[0]
    return "unspecified"


def card(unit, kind, start, end, value, sentence_span, clause_span=None):
    """Subjects and qualifiers come from the clause around the span; when the clause names no
    subject, the subjects of the whole sentence are used and the card says so."""
    text = unit["text"]
    sentence = text[sentence_span[0]:sentence_span[1]]
    clause = text[clause_span[0]:clause_span[1]] if clause_span else sentence
    span_text = text[start:end]
    subjects, scope = mentions(clause, SUBJECTS), "clause"
    if not subjects:
        subjects, scope = mentions(sentence, SUBJECTS), "sentence"
    identity = f"{unit['source_id']}|{unit['locator']}|{kind}|{start}|{end}"
    return {"card_id": sha256(identity.encode()).hexdigest()[:16], "type": kind,
            "source_id": unit["source_id"], "locator": unit["locator"], "unit_kind": unit.get("evidence_kind"),
            "unit_text_sha256": unit["text_sha256"], "span": [start, end], "text": span_text, "value": value,
            "sentence": sentence.strip(), "subjects": subjects, "subjects_from": scope,
            "qualifiers": mentions(clause, QUALIFIERS), "extractor": VERSION}


def unit_cards(unit, columns=None):
    text, kind = unit["text"], unit.get("evidence_kind")
    spans = sentences(text)
    clauses = [(m.start(), m.end()) for m in CLAUSE.finditer(text) if m.group().strip()]
    cards, taken = [], []

    def free(start, end):
        return not any(a < end and start < b for a, b in taken)

    def add(card_kind, start, end, value):
        taken.append((start, end))
        cards.append(card(unit, card_kind, start, end, value, around(text, spans, start, end),
                          around(text, clauses, start, end)))

    # Identifiers and calendar values first, so their digits are never read as quantities.
    for match in DOCUMENT_NUMBER.finditer(text):
        lead = LEAD.match(match.group())
        start = match.start() + (lead.end() if lead else 0)
        number = re.sub(r"\s+", "", text[start:match.end()])
        taken.append((match.start(), start))
        add("document_number", start, match.end(), {"number": number, "key": _document_number_key(number)})
    for match in FULL_DATE.finditer(text):
        if free(match.start(), match.end()):
            parts = re.findall(r"\d+", match.group())
            add("date", match.start(), match.end(), {"date": "-".join(p.zfill(2) for p in parts),
                                                     "role": date_role(text, match.start(), match.end(), kind)})
    for match in YEAR.finditer(text):
        if free(match.start(), match.end()):
            add("year", match.start(), match.end(), re.match(r"\d{4}", match.group()).group())
    for pattern, card_kind in ((AMOUNT, "amount"), (DURATION, "duration"), (COUNT, "count")):
        for match in pattern.finditer(text):
            if free(match.start(), match.end()):
                add(card_kind, match.start(), match.end(),
                    {"number": unit_number(match["number"]), "unit": match["unit"].lstrip("个")})
    for name, value, address in (columns or []):
        start = text.find(value)
        if start >= 0:
            cards.append({**card(unit, "table_cell", start, start + len(value), {"column": name, "cell": address},
                                 (0, len(text))), "subjects": mentions(text, SUBJECTS)})
    return sorted(cards, key=lambda c: (c["span"][0], c["type"]))


def extract(units):
    columns = table_columns(units)
    cards = []
    for unit in units:
        cards.extend(unit_cards(unit, columns.get((unit["source_id"], unit["locator"]))))
    return cards


def verify_cards(cards, units):
    """Every card's text must be the unit text at its span; returns the ids that are not."""
    by_key = {(u["source_id"], u["locator"]): u["text"] for u in units}
    return [c["card_id"] for c in cards
            if by_key.get((c["source_id"], c["locator"]), "")[c["span"][0]:c["span"][1]] != c["text"]]


def summarize(cards, units):
    by_type = Counter(c["type"] for c in cards)
    with_cards = {(c["source_id"], c["locator"]) for c in cards}
    number_cards = [c for c in cards if c["type"] in ("amount", "duration", "count")]
    return {"extractor": VERSION, "units": len(units),
            "corpus_unit_sha256": frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True)),
            "cards": len(cards), "cards_sha256": sha256(json.dumps(cards, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "by_type": {t: by_type.get(t, 0) for t in TYPES},
            "by_unit_kind": dict(sorted(Counter(str(c["unit_kind"]) for c in cards).items())),
            "by_source": dict(sorted(Counter(c["source_id"] for c in cards).items())),
            "units_with_cards": len(with_cards), "units_without_cards": len(units) - len(with_cards),
            "number_cards_without_subject": sum(not c["subjects"] for c in number_cards),
            "number_cards_with_qualifier": sum(bool(c["qualifiers"]) for c in number_cards),
            "dates_by_role": dict(sorted(Counter(c["value"]["role"] for c in cards if c["type"] == "date").items())),
            "span_mismatches": len(verify_cards(cards, units)),
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}


SAMPLE_COLUMNS = ["card_id", "type", "source_id", "locator", "text", "value", "subjects", "qualifiers", "sentence",
                  "correct", "note"]
CORRECT = ("是", "否", "部分")


def sample(cards, per_type, seed):
    chosen = []
    for kind in TYPES:
        pool = sorted((c for c in cards if c["type"] == kind), key=lambda c: c["card_id"])
        random.Random(f"{seed}:{kind}").shuffle(pool)
        chosen.extend(pool[:per_type])
    return chosen


def score(rows, cards):
    by_id = {c["card_id"]: c for c in cards}
    problems, counts = [], {}
    for row in rows:
        if row["card_id"] not in by_id:
            problems.append(row["card_id"] + ": 不是本批卡片")
            continue
        value = (row.get("correct") or "").strip()
        if value not in CORRECT:
            problems.append(f"{row['card_id']}: correct 应为 是/否/部分，现为“{value}”")
            continue
        counts.setdefault(by_id[row["card_id"]]["type"], Counter())[value] += 1
    return problems, {kind: dict(sorted(c.items())) | {"checked": sum(c.values())} for kind, c in sorted(counts.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("extract")
    run.add_argument("--corpus", type=Path, required=True)
    run.add_argument("--cards", type=Path, required=True, help="New private JSONL file under .runtime")
    run.add_argument("--summary", type=Path, required=True, help="New JSON file under evidence/")
    draw = sub.add_parser("sample")
    draw.add_argument("--cards", type=Path, required=True)
    draw.add_argument("--per-type", type=int, default=10)
    draw.add_argument("--seed", default="20261115-cards")
    draw.add_argument("--output", type=Path, required=True, help="New private CSV under .runtime")
    mark = sub.add_parser("score")
    mark.add_argument("--cards", type=Path, required=True)
    mark.add_argument("--sheet", type=Path, required=True)
    mark.add_argument("--output", type=Path, help="New JSON file under evidence/; omit to only check the sheet")
    args = parser.parse_args()

    def new_under(path, folder):
        if not path.resolve().is_relative_to((ROOT / folder).resolve()) or path.exists():
            parser.error(f"NEW_FILE_UNDER_{folder.strip('.').upper()}_REQUIRED: {path}")

    if args.command == "extract":
        new_under(args.cards, ".runtime")
        new_under(args.summary, "evidence")
        units = json.loads(args.corpus.read_text("utf-8"))["units"]
        cards = extract(units)
        result = summarize(cards, units)
        if result["span_mismatches"]:
            parser.error(f"SPAN_MISMATCH: {result['span_mismatches']} cards do not match their unit text")
        args.cards.parent.mkdir(parents=True, exist_ok=True)
        with args.cards.open("x", encoding="utf-8") as handle:
            for item in cards:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    cards = [json.loads(line) for line in args.cards.read_text("utf-8").splitlines() if line.strip()]
    if args.command == "sample":
        new_under(args.output, ".runtime")
        chosen = sample(cards, args.per_type, args.seed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SAMPLE_COLUMNS)
            writer.writeheader()
            for item in chosen:
                writer.writerow({"card_id": item["card_id"], "type": item["type"], "source_id": item["source_id"],
                                 "locator": item["locator"], "text": item["text"],
                                 "value": json.dumps(item["value"], ensure_ascii=False),
                                 "subjects": "、".join(item["subjects"]), "qualifiers": "、".join(item["qualifiers"]),
                                 "sentence": item["sentence"], "correct": "", "note": ""})
        print(json.dumps({"output": str(args.output), "rows": len(chosen),
                          "by_type": dict(Counter(c["type"] for c in chosen)), "seed": args.seed}, ensure_ascii=False))
        return
    with args.sheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    problems, counts = score(rows, cards)
    if problems or args.output is None:
        print(json.dumps({"rows": len(rows), "problems": problems}, ensure_ascii=False, indent=1))
        sys.exit(1 if problems else 0)
    new_under(args.output, "evidence")
    result = {"extractor": VERSION, "rows": len(rows), "by_type": counts,
              "cards_sha256": sha256(json.dumps(cards, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              "note": "Precision on a stratified sample marked by a person; recall is not measured."}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

"""Prepare the historical fact-card sample and count actual developer ratings, offline.

No model, network, approval, ontology write, or inferred human decision. Reading
materials stay under .runtime; the public ledger contains IDs and ratings only.
Prepare refuses to replace any existing evidence. Score validates IDs against
the frozen manifest and treats blank rows as pending, never as correct.
"""
import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path

import extract_fact_cards as facts

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/competition/dual-track-improvement-202611"
PRIVATE = ROOT / ".runtime/dual-track-improvement-202611"
FIELDS = ["item_id", "batch", "track", "review_kind", "reviewer", "decision", "reviewed_at", "note"]
DECISIONS = ("正确", "部分正确", "错误", "无法判断")


def score(rows, manifest):
    expected = {item["item_id"]: item for item in manifest["items"]}
    if len(expected) != len(rows) or {row["item_id"] for row in rows} != set(expected):
        raise ValueError("REVIEW_IDS_MISSING_DUPLICATED_OR_FOREIGN")
    counts, by_type, by_batch = Counter(), {}, {}
    for row in rows:
        item = expected[row["item_id"]]
        if any(row[key] != item[key] for key in ("batch", "track", "review_kind")):
            raise ValueError("REVIEW_ITEM_METADATA_CHANGED")
        decision = row["decision"]
        if not decision:
            if any(row[key] for key in ("reviewer", "reviewed_at", "note")):
                raise ValueError("PENDING_ROW_HAS_REVIEW_CONTENT")
            counts["pending"] += 1
            continue
        if decision not in DECISIONS or row["reviewer"] != "开发者本人" or not row["reviewed_at"].strip():
            raise ValueError("ACTUAL_DEVELOPER_RATING_AND_TIMESTAMP_REQUIRED")
        if decision != "正确" and not row["note"].strip():
            raise ValueError("NON_CORRECT_RATING_REQUIRES_REASON")
        counts[decision] += 1
        by_type.setdefault(item["type"], Counter())[decision] += 1
        by_batch.setdefault(item["batch"], Counter())[decision] += 1
    completed = sum(counts[d] for d in DECISIONS)
    judged = completed - counts["无法判断"]
    return {"review_kind": "开发者人工审核", "total": len(rows), "completed": completed,
            "pending": counts["pending"], "counts": {d: counts[d] for d in DECISIONS},
            "strict_correct_over_completed": counts["正确"] / completed if completed else None,
            "strict_correct_over_judged": counts["正确"] / judged if judged else None,
            "by_type": by_type, "by_batch": by_batch,
            "note": "分层卡片样本精确率；不能当作整体本体语义质量、召回率或最终问答正确率。"}


def prepare(corpus):
    data = json.loads(corpus.read_text("utf-8"))
    units = data["units"]
    cards = facts.extract(units)
    summary = facts.summarize(cards, units)
    historic = json.loads((ROOT / "evidence/fact-cards/summary-20261115-1.json").read_text("utf-8"))
    for key in ("cards_sha256", "corpus_unit_sha256", "script_sha256"):
        if summary[key] != historic[key]:
            raise ValueError("HISTORICAL_FINGERPRINT_MISMATCH:" + key)
    if summary["span_mismatches"]:
        raise ValueError("CARD_SPAN_MISMATCH")
    chosen = facts.sample(cards, 10, "20261115-cards")
    if len(chosen) != 63:
        raise ValueError("EXPECTED_63_HISTORICAL_SAMPLE_ITEMS")
    outputs = [PRIVATE / "manifest.json", REPORT / "human-review.csv", REPORT / "review-manifest.json"]
    if PRIVATE.exists() or any(p.exists() for p in outputs):
        raise ValueError("EXISTING_EVIDENCE_PRESERVED")
    PRIVATE.mkdir(parents=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    by_unit = {(u["source_id"], u["locator"]): u for u in units}
    sources = {s["source_id"]: s for s in data["manifest"]["sources"]}
    public, rows, batches = [], [], {}
    for index, card in enumerate(chosen):
        batch = f"F{index // 9 + 1:02d}"
        entry = {"item_id": card["card_id"], "batch": batch, "track": "A3",
                 "review_kind": "开发者人工审核", "type": card["type"]}
        public.append(entry)
        rows.append({**{key: entry[key] for key in FIELDS if key in entry},
                     **{key: "" for key in FIELDS if key not in entry}})
        source = sources[card["source_id"]]
        full = by_unit[card["source_id"], card["locator"]]
        block = (f"## {index + 1}. {card['card_id']} · {card['type']}\n\n"
                 f"来源：{card['source_id']}；定位：{card['locator']}\n\n"
                 f"原件 URL：{source['url']}\n\n"
                 f"原件 SHA-256：{source['original_sha256']}；修订：{source['revision']}\n\n"
                 f"候选值：`{json.dumps(card['value'], ensure_ascii=False)}`\n\n"
                 f"候选对象：{'、'.join(card['subjects']) or '未识别'}；对象范围：{card['subjects_from']}\n\n"
                 f"候选条件：{'、'.join(card['qualifiers']) or '未识别'}\n\n"
                 f"字符定位：{card['span']}；精确片段：{card['text']}\n\n"
                 f"完整单元（{full['evidence_kind']}）：\n\n{full['text']}\n\n"
                 "判定：____；理由：____\n\n")
        batches.setdefault(batch, []).append(block)
    header = ("# 开发者人工审核：事实卡片\n\n"
              "每项判断类型、值、对象、条件和来源定位是否都正确。选择：正确 / 部分正确 / 错误 / 无法判断。"
              "非“正确”请写一句理由。评分不构成更新审批。\n\n"
              "归档文本不代表现行有效。扫描转录是 AI 产物，未经逐字人工核验，不能作为已核验原件。"
              "本批按原种子抽样顺序发放，不挑选正确条目；每批9项。\n\n")
    for batch, blocks in batches.items():
        (PRIVATE / f"batch-{batch}.md").write_text(header + "".join(blocks), "utf-8")
    (PRIVATE / "cards.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), "utf-8")
    (PRIVATE / "sample.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), "utf-8")
    manifest = {"seed": "20261115-cards", "per_type": 10, "batch_size": 9,
                "historical_sample_original_file_found": False,
                "sample_status": "deterministic_reconstruction_same_extractor_corpus_and_seed",
                "corpus_unit_sha256": summary["corpus_unit_sha256"], "cards_sha256": summary["cards_sha256"],
                "corpus_file_sha256": sha256(corpus.read_bytes()).hexdigest(),
                "sample_sha256": sha256((PRIVATE / "sample.json").read_bytes()).hexdigest(),
                "model_calls": 0, "items": public}
    for path in (PRIVATE / "manifest.json", REPORT / "review-manifest.json"):
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    with (REPORT / "human-review.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return score(rows, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--corpus", type=Path, required=True)
    sub.add_parser("score")
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.corpus)
    else:
        with (REPORT / "human-review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        manifest = json.loads((REPORT / "review-manifest.json").read_text("utf-8"))
        result = score(rows, manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

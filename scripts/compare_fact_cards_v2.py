"""One offline paired extraction over pinned units; preserve all prior reviews.

This is development replay of known cases, not an independent quality evaluation.
Candidate text stays private; the report holds counts, IDs and fingerprints.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import extract_fact_cards as baseline
import extract_fact_cards_v2 as candidate

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/competition/dual-track-improvement-202611"
PRIVATE = ROOT / ".runtime/dual-track-improvement-202611"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def fingerprint(cards):
    return sha256(json.dumps(cards, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def binding_errors(cards, units):
    by_unit = {(u["source_id"], u["locator"]): u for u in units}
    errors = []
    for card in cards:
        if card["type"] != "table_cell":
            continue
        unit = by_unit[card["source_id"], card["locator"]]
        span = candidate.cell_spans(unit).get(card["value"]["cell"])
        if span is None or list(span) != card["span"]:
            errors.append(card["card_id"])
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--tests", required=True, type=Path)
    args = parser.parse_args()
    out = PRIVATE / "fact-cards-v2"
    summary_file = REPORT / "fact-cards-v2-offline.json"
    if out.exists() or summary_file.exists():
        raise ValueError("EXISTING_RESULTS_PRESERVED")
    preserved = [REPORT / "human-review.csv", REPORT / "human-review-summary.json",
                 PRIVATE / "sample.json", PRIVATE / "cards.jsonl", REPORT / "review-manifest.json"]
    original_hashes = {p.name: digest(p) for p in preserved}
    corpus = json.loads(args.corpus.read_text("utf-8"))
    units = corpus["units"]
    historical = json.loads((ROOT / "evidence/fact-cards/summary-20261115-1.json").read_text("utf-8"))
    before = time.perf_counter()
    old = baseline.extract(units)
    elapsed_old = time.perf_counter() - before
    assert fingerprint(old) == historical["cards_sha256"]
    assert baseline.frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True)) == historical["corpus_unit_sha256"]
    assert digest(ROOT / "scripts/extract_fact_cards.py") == historical["script_sha256"]
    assert old == [json.loads(line) for line in (PRIVATE / "cards.jsonl").read_text("utf-8").splitlines()]
    before = time.perf_counter()
    new = candidate.extract(units)
    elapsed_new = time.perf_counter() - before
    assert baseline.verify_cards(new, units) == []
    assert not binding_errors(new, units)
    assert len({c["card_id"] for c in new}) == len(new)
    sample = json.loads((PRIVATE / "sample.json").read_text("utf-8"))
    by_id = {c["card_id"]: c for c in new}
    changes = []
    for number, card in enumerate(sample, 1):
        after = by_id.get(card["card_id"])
        if after is None:
            matches = [c for c in new if (c["source_id"], c["locator"]) == (card["source_id"], card["locator"])
                       and ((card["type"] == "table_cell" and c["type"] == "table_cell" and c["value"] == card["value"])
                            or (card["type"] == "duration" and c["type"] == "count" and c["span"][0] == card["span"][0]
                                and c["value"]["unit"] == "周岁"))]
            assert len(matches) <= 1
            after = matches[0] if matches else None
            changes.append({"number": number, "before": card, "after": after})
    assert [item["number"] for item in changes] == [11, 13, 14, 55, 56]
    old_by_id = {}
    for card in old:
        old_by_id.setdefault(card["card_id"], []).append(card)
    # Apart from the explicitly versioned tag, all retained identities must stay unchanged.
    # v1's first-find bug can assign identical IDs to two different table cells.
    # Preserve all original entries; do not silently collapse them to the last one.
    assert all(any({k:v for k,v in c.items() if k != "extractor"} ==
                   {k:v for k,v in previous.items() if k != "extractor"}
                   for previous in old_by_id[c["card_id"]])
               for c in new if c["card_id"] in old_by_id)
    out.mkdir()
    (out / "cards-v2.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in new), "utf-8")
    (out / "known-case-comparison.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2), "utf-8")
    by_unit = {(u["source_id"], u["locator"]): u for u in units}
    blocks = ["# 已知问题开发对照：5项\n\n原判定不改，本页不是独立留出或新版人工成绩。"
              "第14项被过滤而无新卡片；请核过滤是否合理。其余项需核类型/单位或单元格字符区间，"
              "对象和条件仍可能缺失。不得因单项机械问题修复就自动判整张正确。\n\n"]
    for item in changes:
        card, after = item["before"], item["after"]
        blocks.append(f"## 原第{item['number']}项\n\n来源：{card['source_id']} · {card['locator']}\n\n"
                      "V1：\n```json\n" + json.dumps(card, ensure_ascii=False, indent=2) + "\n```\n\n"
                      "V2：\n```json\n" + json.dumps(after, ensure_ascii=False, indent=2) + "\n```\n\n"
                      "冻结完整单元：\n\n" + by_unit[card["source_id"], card["locator"]]["text"] + "\n\n"
                      "V2本人判断：____；理由：____\n\n")
    (out / "known-case-review.md").write_text("".join(blocks), "utf-8")
    test_suites = list(ET.parse(args.tests).getroot().iter("testsuite"))
    result = {"experiment": "offline_known_case_development_replay", "baseline_version": baseline.VERSION,
              "candidate_version": candidate.VERSION, "corpus_units": len(units),
              "corpus_unit_sha256": historical["corpus_unit_sha256"], "baseline_cards_sha256": fingerprint(old),
              "candidate_cards_sha256": fingerprint(new), "baseline_cards": len(old), "candidate_cards": len(new),
              "by_type_before": dict(Counter(c["type"] for c in old)),
              "by_type_after": dict(Counter(c["type"] for c in new)),
              "cell_span_binding_errors_before": len(binding_errors(old, units)),
              "cell_span_binding_errors_after": len(binding_errors(new, units)),
              "exact_span_mismatches_before": len(baseline.verify_cards(old, units)),
              "exact_span_mismatches_after": len(baseline.verify_cards(new, units)),
              "sample_changed_numbers": [c["number"] for c in changes], "sample_unchanged": 58,
              "baseline_duplicate_card_id_entries": len(old) - len(old_by_id),
              "candidate_duplicate_card_id_entries": len(new) - len(by_id),
              "retained_card_ids": len(set(old_by_id) & set(by_id)),
              "removed_card_ids": len(set(old_by_id) - set(by_id)), "added_card_ids": len(set(by_id) - set(old_by_id)),
              "wall_seconds_single_local_run": {"baseline": elapsed_old, "candidate": elapsed_new},
              "tests": {key:sum(int(s.get(key, "0")) for s in test_suites) for key in ("tests","failures","errors","skipped")},
              "source_sha256": {p.name:digest(p) for p in (ROOT / "scripts/extract_fact_cards.py",
                                                         ROOT / "scripts/extract_fact_cards_v2.py", Path(__file__))},
              "new_model_calls": 0, "token_cost": 0, "new_human_scores": 0,
              "business_quality_measured": False, "old_evidence_sha256": original_hashes,
              "candidate_private_file_sha256": digest(out / "cards-v2.jsonl"),
              "limitations": ["已知样本开发对照，非独立留出", "对象/条件/日期角色/完整文号仍有缺口",
                              "单次本地计时不用于性能推广", "未装载问答或Nexent，未改正式状态"]}
    assert {p.name:digest(p) for p in preserved} == original_hashes
    result["old_evidence_unchanged"] = True
    summary_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

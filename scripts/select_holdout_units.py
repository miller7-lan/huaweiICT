"""Select six held-out units for the low-resource construction re-test, mechanically.

The units come from the frozen corpus, never from U1-U6, and are drawn with a fixed seed so the
choice is reproducible and not hand-picked. Strata: three articles of the Henan household
registration norm, and one paragraph or row each from three other sources. Each unit's target is
assigned by keyword rules from its own text before any model call, so no target is tuned to an output.

  python scripts/select_holdout_units.py --corpus <591 corpus.json> --output evaluation/lowresource-20261126/units-holdout.json
"""
import argparse
import hashlib
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = "20261126-holdout"
USED = ROOT / "evaluation/lowresource-20261030/units.json"
MIN_CHARS, MAX_CHARS = 80, 800
STRATA = [("HN-NORM-PUBLISHER", r"^第.+条$", 3), ("MOF-FIRST-ISSUE-2018", r"^正文段落", 1),
          ("HN-MEASURES-2024-JY", r"^正文段落", 1), ("HN-FEE-CATALOG-XLSX-2025", r"第\d+行$", 1)]
TARGETS = [
    ("value", r"\d+(?:\.\d+)?元", "收费或数值：每个分支的金额、计量单位、适用对象或情形及减免例外；不推断现行应缴金额"),
    ("material", r"材料|证明|凭|户口簿|身份证", "办理材料：按情形或分支区分需要提交或交验的材料；写成 Matter -requires-> Material，不跨分支合并"),
    ("time_limit", r"\d+(?:个)?(?:工作日|自然日|日|天|个月|年)", "时限：取值、单位（工作日/自然日）、起算点和适用对象；不做单位换算"),
    ("condition", r"", "适用条件：对象、条件、受理或核准机关及例外情形；条件结果只能取 true/false/unknown"),
]


def used_locators():
    plan = json.loads(USED.read_text("utf-8"))
    return {(s["source_id"], loc["locator"]) for u in plan["units"] for s in u["sources"] for loc in s["locators"]}


def target_for(text):
    for kind, pattern, target in TARGETS:
        if not pattern or re.search(pattern, text):
            return kind, target


def select(corpus_units):
    rng, excluded, chosen = random.Random(SEED), used_locators(), []
    for source, locator_pattern, count in STRATA:
        pool = sorted((u for u in corpus_units if u["source_id"] == source and re.search(locator_pattern, u["locator"])
                       and MIN_CHARS <= len(u["text"]) <= MAX_CHARS and (source, u["locator"]) not in excluded),
                      key=lambda u: u["locator"])
        chosen.extend(rng.sample(pool, count))
    units = []
    for n, unit in enumerate(chosen, 1):
        kind, target = target_for(unit["text"])
        units.append({"id": f"H{n}", "kind": kind, "seen_in_development": False, "target": target,
                      "sources": [{"source_id": unit["source_id"],
                                   "locators": [{"locator": unit["locator"], "text_sha256": unit["text_sha256"]}]}]})
    return units


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("OUTPUT_EXISTS")
    corpus = json.loads(args.corpus.read_text("utf-8"))["units"]
    plan = {"schema_version": 1, "purpose": "held-out units for the low-resource construction re-test (B1 vs B2)",
            "selection": {"seed": SEED, "strata": [{"source": s, "locator": p, "count": c} for s, p, c in STRATA],
                          "chars": [MIN_CHARS, MAX_CHARS], "excluded": "every locator of U1-U6",
                          "target_rule": [k for k, _, _ in TARGETS],
                          "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "corpus_unit_count": len(corpus), "units": select(corpus)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps([(u["id"], u["kind"], u["sources"][0]["source_id"], u["sources"][0]["locators"][0]["locator"])
                      for u in plan["units"]], ensure_ascii=False))


if __name__ == "__main__":
    main()

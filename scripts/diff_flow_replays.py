"""Question-by-question difference between two text-free replay files of the same drafts.

  python scripts/diff_flow_replays.py OLD.json NEW.json [--expected REFERENCE.json] [--output evidence/....json]

OLD and NEW are written by export_flow_v3_diagnostics.py from the same records.jsonl and
corpus (OLD: the T2 replay of the frozen code; NEW: the V2 replay of V3.1). For every
question and method it reports the replayed result kind before and after and, per claim,
the failure-code kinds that only the new checks raise. With --expected (the private
reference; only id -> expected outcome is read) each change is also classified:

  LEAK             expected stop, now answered or partially answered        must be fixed
  NEW_FALSE_STOP   expected answer, now stopped but not before                 must be explained
  DOWNGRADED       answer kind became partial_answer or lost claims            check the rule
  RECOVERED        expected answer, stopped before, answered now
  OTHER            any other change

"rule_kinds_new_only" counts, over all claims, the failure kinds raised by the new code
but not by the old: the first place to look for a new rule that rejects faithful claims.
The output holds ids, kinds and counts only. These are replays of stored drafts, not results.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ANSWERED = {"answer", "answer_with_gaps", "partial_answer"}


def kind_of(code):
    """"A3_BRANCH_MISMATCH:20元" -> "BRANCH_MISMATCH"."""
    return re.sub(r"^A\d+_", "", code).split(":")[0]


def by_key(data):
    return {(r["question_id"], r["compared_as"]): r for r in data["records"]}


def replayed(record):
    return (record.get("last_draft_replayed") or {}).get("result_kind")


def claim_kinds(record, field="fails_alone"):
    return [{kind_of(code) for code in claim.get(field) or []} for claim in record.get("last_draft_claims") or []]


def classify(expected, old_kind, new_kind, old_rec, new_rec):
    if expected == "stop" and new_kind in ANSWERED:
        return "LEAK"
    if expected == "answer" and new_kind == "stop" and old_kind != "stop":
        return "NEW_FALSE_STOP"
    if expected == "answer" and old_kind == "stop" and new_kind in ANSWERED:
        return "RECOVERED"
    old_n = (old_rec.get("last_draft_replayed") or {}).get("final_assertions") or 0
    new_n = (new_rec.get("last_draft_replayed") or {}).get("final_assertions") or 0
    if (old_kind in ("answer", "answer_with_gaps") and new_kind == "partial_answer") or \
            (new_kind in ANSWERED and old_kind in ANSWERED and new_n < old_n):
        return "DOWNGRADED"
    return "OTHER"


def diff(old, new, expected=None):
    problems = [f"{name.upper()}_DIFFERS" for name in ("records_sha256", "corpus_sha256") if old.get(name) != new.get(name)]
    if problems:
        return {"comparable": False, "problems": problems}
    before, after = by_key(old), by_key(new)
    changes, new_only = [], Counter()
    for key in sorted(set(before) & set(after)):
        old_rec, new_rec = before[key], after[key]
        old_claims, new_claims = claim_kinds(old_rec), claim_kinds(new_rec)
        added = []
        for index, kinds in enumerate(new_claims):
            extra = kinds - (old_claims[index] if index < len(old_claims) else set())
            new_only.update(extra)
            if extra:
                added.append({"claim": index + 1, "new_kinds": sorted(extra)})
        old_kind, new_kind = replayed(old_rec), replayed(new_rec)
        if old_kind == new_kind and not added:
            continue
        exp = (expected or {}).get(key[0])
        row = {"question_id": key[0], "method": key[1], "expected": exp, "old": old_kind, "new": new_kind,
               "gate_new": (new_rec.get("last_draft_replayed") or {}).get("gate"),
               "claims_new_failures": added}
        if expected is not None:
            row["class"] = classify(exp, old_kind, new_kind, old_rec, new_rec) if old_kind != new_kind else (
                "DOWNGRADED" if classify(exp, old_kind, new_kind, old_rec, new_rec) == "DOWNGRADED" else "RULES_ONLY")
        changes.append(row)
    classes = Counter(row.get("class", "UNCLASSIFIED") for row in changes)
    return {"comparable": True, "scope": "replay of stored drafts; ids, kinds and counts only; not a new result",
            "pairs_compared": len(set(before) & set(after)), "pairs_changed": len(changes),
            "classes": dict(sorted(classes.items())), "rule_kinds_new_only": dict(new_only.most_common()),
            "must_fix": [f"{r['question_id']} {r['method']}" for r in changes if r.get("class") == "LEAK"],
            "must_explain": [f"{r['question_id']} {r['method']}" for r in changes if r.get("class") == "NEW_FALSE_STOP"],
            "changes": changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--expected", type=Path, help="Private reference; only id -> expected outcome is read")
    parser.add_argument("--output", type=Path, help="New JSON file under evidence/")
    args = parser.parse_args()
    expected = None
    if args.expected:
        from export_flow_v3_diagnostics import load_expected
        expected = load_expected(args.expected)
    result = diff(*(json.loads(path.read_text("utf-8")) for path in (args.old, args.new)), expected)
    if args.output:
        if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
            parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "changes"}, ensure_ascii=False, indent=1))
    sys.exit(2 if not result["comparable"] else (1 if result.get("must_fix") else 0))


if __name__ == "__main__":
    main()

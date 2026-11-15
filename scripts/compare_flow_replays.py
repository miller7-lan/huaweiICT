"""Compare two text-free replay files of the same stored drafts and apply the V3.1 decision rule.

  python scripts/compare_flow_replays.py OLD.json NEW.json [--output evidence/....json]

OLD and NEW are written by export_flow_v3_diagnostics.py with --expected, from the same
records.jsonl and the same corpus (the hashes are compared; a mismatch is refused). Only
the "by_expected_outcome" counts are read. For every compared method:

  should-stop kept    NEW stops on expected-stop questions at least as often as OLD
  no new false stops  NEW stops on expected-answer questions no more often than OLD

Exit status 0: both hold for every method. 1: a condition fails (V3.1 is not adopted;
report to the user). 2: the files are not comparable. Nothing calls a model; the output
holds counts only. These are replays of stored drafts, not new results.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def counts(table, expected, prefix="replayed:"):
    row = table.get(expected, {})
    return {key[len(prefix):]: value for key, value in row.items() if key.startswith(prefix)}


def compare(old, new):
    problems = [f"{name.upper()}_DIFFERS" for name in ("records_sha256", "corpus_sha256", "expected_sha256")
                if old.get(name) != new.get(name)]
    if "by_expected_outcome" not in old or "by_expected_outcome" not in new:
        problems.append("EXPECTED_COUNTS_MISSING: export both with --expected")
    if problems:
        return {"comparable": False, "problems": problems}
    methods, failures = {}, []
    for method in sorted(set(old["by_expected_outcome"]) & set(new["by_expected_outcome"])):
        before, after = old["by_expected_outcome"][method], new["by_expected_outcome"][method]
        row = {}
        for expected in ("expected_stop", "expected_answer"):
            row[expected] = {"total": sum(counts(after, expected).values()),
                             "old": counts(before, expected), "new": counts(after, expected)}
        stop_old, stop_new = (row["expected_stop"][side].get("stop", 0) for side in ("old", "new"))
        false_old, false_new = (row["expected_answer"][side].get("stop", 0) for side in ("old", "new"))
        row["should_stop_kept"] = stop_new >= stop_old
        row["no_new_false_stops"] = false_new <= false_old
        row["should_stop_all_stopped"] = stop_new == row["expected_stop"]["total"]
        if not row["should_stop_kept"]:
            failures.append(f"{method}: SHOULD_STOP_FEWER_STOPS {stop_old}->{stop_new}")
        if not row["no_new_false_stops"]:
            failures.append(f"{method}: MORE_FALSE_STOPS {false_old}->{false_new}")
        methods[method] = row
    return {"comparable": True, "scope": "replay of stored drafts, counts only; not a new result",
            "old_flow_sha256": old.get("flow_sha256"), "new_flow_sha256": new.get("flow_sha256"),
            "methods": methods, "failures": failures, "adopt_v31": not failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--output", type=Path, help="New JSON file under evidence/")
    args = parser.parse_args()
    result = compare(*(json.loads(path.read_text("utf-8")) for path in (args.old, args.new)))
    if args.output:
        if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
            parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(2 if not result["comparable"] else (0 if result["adopt_v31"] else 1))


if __name__ == "__main__":
    main()

"""Compare the Jev claim judge with the two human reviewers' citation judgement (Y6).

Per output (question x method) that a reviewer rated citations_support 是 or 否, Jev's view is
  是  every judged claim of that output was "supported";
  否  at least one claim was "unsupported";
  不确定  otherwise (some "uncertain", none unsupported) - counted apart, never forced into 是/否.
Outputs with no judged claim are skipped and counted. The result holds counts and question ids only.

  python scripts/jev_human_agreement.py --jev <private claims.json> --pack <review pack dir> \
      --sheets <sheet-A.csv> <sheet-B.csv> --output evidence/<new>.json

Run it only after score_flow_v3_review.py --check has passed for both sheets. No model call.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from score_flow_v3_review import read_sheet, sheet_reviewer  # noqa: E402


def jev_view(results):
    verdicts = defaultdict(list)
    for row in results:
        verdicts[(row["question_id"], row["method"])].append(row["verdict"])
    view = {}
    for output, found in verdicts.items():
        if "unsupported" in found:
            view[output] = "否"
        elif all(v == "supported" for v in found):
            view[output] = "是"
        else:
            view[output] = "不确定"
    return view


def compare(view, key, sheets):
    """sheets: {reviewer: {(question, label): values}}."""
    out = {"by_reviewer": {}, "skipped_no_jev_claims": 0}
    skipped = set()
    for reviewer, sheet in sheets.items():
        matrix, differ = Counter(), []
        for (question, label), values in sorted(sheet.items()):
            human = values["citations_support"]
            if human not in ("是", "否"):
                continue
            method = key[question][label]
            jev = view.get((question, method))
            if jev is None:
                skipped.add((question, method))
                continue
            matrix[f"人工{human}/Jev{jev}"] += 1
            if jev != "不确定" and jev != human:
                differ.append(f"{question}:{method}")
        decided = sum(n for k, n in matrix.items() if not k.endswith("不确定"))
        agree = matrix["人工是/Jev是"] + matrix["人工否/Jev否"]
        out["by_reviewer"][reviewer] = {"matrix": dict(sorted(matrix.items())),
                                        "agreement_when_jev_decided": f"{agree}/{decided}",
                                        "disagreements": differ}
    out["skipped_no_jev_claims"] = len(skipped)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jev", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--sheets", type=Path, nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or (ROOT / "evidence") not in output.parents:
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
    key = json.loads((args.pack / "key.json").read_text("utf-8"))
    allowed = json.loads((args.pack / "pack.json").read_text("utf-8"))["reviewers"]
    sheets = {sheet_reviewer(p, allowed): read_sheet(p, key) for p in args.sheets}
    if len(sheets) != 2:
        parser.error("SAME_REVIEWER_ON_TWO_SHEETS")
    run = json.loads(args.jev.read_text("utf-8"))
    result = {"scope": "Jev claim judge vs human citations_support; counts and question ids only",
              "jev_rule": "是 = all claims supported; 否 = any unsupported; 不确定 = otherwise",
              "jev_claims": len(run["results"]), **compare(jev_view(run["results"]), key, sheets),
              "use": "decides whether Jev may be cited as a second automatic measure; changes no answer"}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

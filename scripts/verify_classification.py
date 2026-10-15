"""Replay API contract fixtures, or explicitly evaluate Jev on the same synthetic set.

Replay metrics are routing/serialization checks, never Jev accuracy measurements.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.classification import classify_corpus, save_run


def example_corpus(cases):
    return {"data_class": "synthetic", "dataset_id": "classification-engineering-examples-v1", "assets": [
        {"asset_id": case["id"], "source_id": case["id"], "sha256": hashlib.sha256(case["text"].encode()).hexdigest(),
         "title": case["title"], "modality": "text", "segments": [{"asset_id": case["id"],
         "segment_id": case["id"] + "-s1", "content": case["text"], "locator": {"kind": "line", "line": 1}}]}
        for case in cases]}


class ContractReplay:
    name = "contract_replay_not_jev"

    def __init__(self, cases):
        self.cases = cases

    def evaluate(self, state, questions, model):
        case = next(c for c in self.cases if c["title"] == state["title"])
        answers = {}
        for key, question in questions.items():
            if question["type"] == "choice":
                answers[key] = {"type": "choice", "choice": case["document_type"], "confidence": .99,
                    "probabilities": {label: float(label == case["document_type"]) for label in question["criteria"]}}
            else:
                group, label = key.split(":")
                answers[key] = {"type": "noul", "noul": .99 if label in case[group] else .01}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}


def measure(run, cases):
    totals = {key: 0 for key in ("document_type_correct", "domains_exact", "purposes_exact", "routes_exact")}
    by_id = {r["asset_id"]: r for r in run["records"]}
    for case in cases:
        r = by_id[case["id"]]
        valid = r["status"] != "error"
        totals["document_type_correct"] += bool(valid and r["document_type"] and r["document_type"]["choice"] == case["document_type"])
        totals["domains_exact"] += bool(valid and r["domains"] and set(r["domains"]["labels"]) == set(case["domains"]))
        totals["purposes_exact"] += bool(valid and r["purposes"] and set(r["purposes"]["labels"]) == set(case["purposes"]))
        totals["routes_exact"] += bool(valid and set(r["routes"]) == set(case["routes"]))
    return {"sample_count": len(cases), **totals,
            "routing_errors": len(cases) - totals["routes_exact"],
            "routing_error_definition": "expected route set differs or classification failed; includes conservative deferrals"}


def order_comparison(left, right):
    changed, comparable = [], 0
    for a, b in zip(left["records"], right["records"]):
        if a["status"] == "error" or b["status"] == "error":
            continue
        comparable += 1
        def decisions(r):
            return (r["document_type"]["choice"], sorted(r["domains"]["labels"]),
                    sorted(r["purposes"]["labels"]), r["routes"])
        if decisions(a) != decisions(b):
            changed.append(a["segment_id"])
    return {"comparable_count": comparable, "changed_segment_ids": changed,
            "stable": not changed if comparable else None}


def integrity(run, corpus):
    originals = {s["segment_id"]: s for a in corpus["assets"] for s in a["segments"]}
    seen = set()
    for rows in run["bundles"].values():
        assert len(rows) == len({row["segment_id"] for row in rows})
        for row in rows:
            original = originals[row["segment_id"]]
            assert row["content"] == original["content"] and row["locator"] == original["locator"]
            seen.add(row["segment_id"])
    assert seen == set(originals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Calls paid Jev on synthetic examples only; needs TYPESAFE_API_KEY")
    args = parser.parse_args()
    cases = json.loads((ROOT / "config/preparation/classification.examples.json").read_text(encoding="utf-8"))["cases"]
    config = json.loads((ROOT / "config/preparation/classification.json").read_text(encoding="utf-8"))
    corpus = example_corpus(cases)
    before = deepcopy(corpus)
    provider = None if args.live else ContractReplay(cases)
    runs = [classify_corpus(corpus, config, provider, reverse_choices=reverse) for reverse in (False, True)]
    paths = []
    for run in runs:
        integrity(run, corpus)
        paths.append(str(save_run(run, ROOT / ".runtime/preparation/classification").relative_to(ROOT)))
    assert corpus == before
    errors = any(run["summary"]["error_codes"] for run in runs)
    report = {"mode": "live_jev" if args.live else "contract_replay_not_jev", "captured_at": runs[0]["captured_at"],
              "fixtures_are_synthetic": True, "independent_business_evaluation": False,
              "live_accuracy_measured": args.live and not errors,
              "all_originals_preserved": True, "source_corpus_unchanged": True,
              "runs": [{"private_output": path, **run["summary"]} for path, run in zip(paths, runs)],
              "order_comparison": order_comparison(*runs),
              "fixture_checks" if not args.live else "live_example_metrics": [measure(run, cases) for run in runs]}
    if not args.live:
        assert all(m["routing_errors"] == 0 for m in report["fixture_checks"])
        assert report["order_comparison"]["stable"]
    directory = ROOT / "evidence/jev-classification"
    directory.mkdir(parents=True, exist_ok=True)
    name = ("live-" if args.live else "contract-replay-") + runs[0]["run_id"] + ".json"
    (directory / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str((directory / name).relative_to(ROOT)), "mode": report["mode"],
                      "provider_errors": errors, "live_accuracy_measured": report["live_accuracy_measured"],
                      "order_comparison": report["order_comparison"]}, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

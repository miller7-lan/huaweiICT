"""Compare retrieval only on pinned developer probes; no model, network or old blind scores.

--before is an explicitly trusted local snapshot of frozen_qa.py, not downloaded code.
Outputs contain IDs, locators, hashes and counts; no source text or personal answers.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp import frozen_qa
from gov_mcp.frozen_retrieval import EvidenceIndex


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(corpus_path, cases_path, before_path):
    corpus = json.loads(corpus_path.read_text("utf-8"))
    units = corpus["units"]
    actual = frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True))
    if actual != corpus["manifest"]["corpus_sha256"]:
        raise ValueError("CORPUS_HASH_MISMATCH")
    cases = json.loads(cases_path.read_text("utf-8"))["cases"]
    available = {(u["source_id"], u["locator"]) for u in units}
    for case in cases:
        if any(tuple(target) not in available for target in case.get("targets", [])):
            raise ValueError("PROBE_TARGET_NOT_IN_CORPUS:" + case["id"])
    spec = importlib.util.spec_from_file_location("trusted_before_frozen_qa", before_path)
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    index = EvidenceIndex(units)
    methods = {"literal_top5": lambda q: before.retrieve(units, q),
               "before_linked": lambda q: before.retrieve_evidence(units, q),
               "after_primary": lambda q: primary(index.retrieve(q)),
               "after_with_context": lambda q: index.retrieve(q)["evidence"]}
    rows, gates = [], []
    for case in cases:
        if case.get("gate"):
            gates.append({"id": case["id"], "expected": case["gate"],
                          "before": before.scope_gate(case["question"]),
                          "after": frozen_qa.scope_gate(case["question"])})
            continue
        row = {"id": case["id"], "target_count": len(case["targets"]), "methods": {}}
        for name, retrieve in methods.items():
            started = time.perf_counter()
            hits = retrieve(case["question"])
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            keys = {(u["source_id"], u["locator"]) for u in hits}
            found = [t for t in case["targets"] if tuple(t) in keys]
            if name.startswith("after"):
                plan = index.retrieve(case["question"])["plan"] if name == "after_with_context" else None
                messages = frozen_qa.build_messages(case["question"], hits, "proposed", retrieval_plan=plan)
            else:
                messages = before.build_messages(case["question"], hits, "proposed")
            row["methods"][name] = {"found": len(found), "complete": len(found) == len(case["targets"]),
                                    "missing": [t for t in case["targets"] if tuple(t) not in keys],
                                    "units": len(hits), "characters": sum(len(u["text"]) for u in hits),
                                    "prompt_characters": sum(len(m["content"]) for m in messages),
                                    "retrieval_ms": elapsed}
        rows.append(row)
    totals = {}
    for name in methods:
        totals[name] = {"found": sum(r["methods"][name]["found"] for r in rows),
                        "targets": sum(r["target_count"] for r in rows),
                        "complete_questions": sum(r["methods"][name]["complete"] for r in rows),
                        "questions": len(rows),
                        "mean_units": round(sum(r["methods"][name]["units"] for r in rows) / len(rows), 2),
                        "mean_characters": round(sum(r["methods"][name]["characters"] for r in rows) / len(rows), 1),
                        "mean_prompt_characters": round(sum(r["methods"][name]["prompt_characters"] for r in rows) / len(rows), 1)}
    return {"status": "DEVELOPER_RETRIEVAL_COMPARISON_ONLY", "model_calls": 0,
            "business_accuracy_measured": False, "independent_holdout": False,
            "notes": "Known-source developer probes; target locators are not exhaustive semantic gold labels. Context lengths differ. No old formal scores changed.",
            "corpus_sha256": actual, "probes_sha256": digest(cases_path), "before_sha256": digest(before_path),
            "after_source_sha256": {name: digest(ROOT / "src/gov_mcp" / name)
                                    for name in ("frozen_qa.py", "frozen_qa_server.py", "frozen_retrieval.py")},
            "totals": totals, "cases": rows, "gates": gates}


def primary(packet):
    return packet["evidence"][:packet["plan"]["primary_count"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/crossdoc-refactor-20261113/retrieval-probes.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    result = compare(args.corpus, args.cases, args.before)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"totals": result["totals"], "gates": result["gates"], "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

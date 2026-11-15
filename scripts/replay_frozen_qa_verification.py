"""Recheck saved real candidates against their original retrieved evidence, without a model.

This is a verifier regression replay on observed failures, not a fresh model score.
No evidence is added and no candidate, old record or formal evaluation is overwritten.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp import frozen_qa
from compare_frozen_retrieval import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    corpus = json.loads(args.corpus.read_text("utf-8"))
    corpus_sha = frozen_qa.sha(json.dumps(corpus["units"], ensure_ascii=False, sort_keys=True))
    if corpus_sha != corpus["manifest"]["corpus_sha256"]:
        parser.error("CORPUS_HASH_MISMATCH")
    units = {(u["source_id"], u["locator"]): u for u in corpus["units"]}
    results = []
    for line in args.records.read_text("utf-8").splitlines():
        record = json.loads(line)
        evidence = []
        for ref in record["retrieval"]["evidence"]:
            source = units[ref["source_id"], ref["locator"]]
            if source["text_sha256"] != ref["text_sha256"]:
                raise ValueError("RETRIEVED_TEXT_HASH_MISMATCH")
            evidence.append(source)
        if record["model_output"] is None:
            raise ValueError("REPLAY_REQUIRES_ACTUAL_MODEL_CANDIDATE")
        checks = frozen_qa.verify(record["model_output"], evidence)
        results.append({"id": record["question_id"], "method": record["comparison_method"],
                        "original_final_outcome": record["final"]["outcome"], "original_checks": record["checks"],
                        "replay_checks": checks, "replay_final_outcome": record["model_output"]["outcome"] if checks["passed"] else "stop"})
    result = {"status": "OBSERVED_MODEL_CANDIDATE_VERIFIER_REPLAY_ONLY", "model_calls": 0,
              "fresh_model_evaluation": False, "business_accuracy_measured": False,
              "records_sha256": digest(args.records), "corpus_sha256": corpus_sha,
              "verifier_sha256": digest(ROOT / "src/gov_mcp/frozen_qa.py"), "rows": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"counts": {name: {"original_answers": sum(r["original_final_outcome"] == "answer" for r in results if r["method"] == name),
                                        "replay_answers": sum(r["replay_final_outcome"] == "answer" for r in results if r["method"] == name)}
                                for name in ("before", "after")}, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

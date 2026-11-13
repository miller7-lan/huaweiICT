"""Six-question before/after development comparison, dry-run by default.

Explicit --live makes at most 12 existing DeepSeek calls, with no retries.
Raw questions, prompts and answers stay in a new private runtime directory.
This measures engineering outputs, not independent human-rated business accuracy.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp import frozen_qa
from compare_frozen_retrieval import digest

IDS = ("R01", "R02", "R04", "R06", "R07", "R08")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory inside .runtime")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cases", type=Path, help="Optional frozen six-question validation set")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()):
        parser.error("PRIVATE_RUNTIME_OUTPUT_REQUIRED")
    cases_path = args.cases or ROOT / "evaluation/crossdoc-refactor-20261113/retrieval-probes.json"
    cases = [c for c in json.loads(cases_path.read_text("utf-8"))["cases"] if args.cases or c["id"] in IDS]
    corpus = json.loads(args.corpus.read_text("utf-8"))
    units = corpus["units"]
    corpus_sha = frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True))
    if corpus_sha != corpus["manifest"]["corpus_sha256"]:
        parser.error("CORPUS_HASH_MISMATCH")
    if len(cases) != 6 or len({c["id"] for c in cases}) != 6:
        parser.error("EXPECTED_SIX_DISTINCT_PROBES")
    plan = {"purpose": "fresh_questions_pending_scoring" if args.cases else "development_comparison_not_independent_accuracy", "questions": len(cases),
            "ids": [c["id"] for c in cases], "max_model_calls": 12, "max_output_tokens_per_call": 1536, "retries": 0,
            "profile": "existing official DeepSeek; environment/system proxy; TLS verification",
            "corpus_sha256": corpus_sha, "before_sha256": digest(args.before),
            "cases_sha256": digest(cases_path),
            "after_sha256": {name: digest(ROOT / "src/gov_mcp" / name)
                             for name in ("frozen_qa.py", "frozen_retrieval.py", "deepseek.py")}}
    if not args.live:
        print(json.dumps({"dry_run": True, "model_calls": 0, **plan}, ensure_ascii=False))
        return
    if args.output.exists():
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    spec = importlib.util.spec_from_file_location("trusted_before_frozen_qa", args.before)
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    from gov_mcp.deepseek import PROFILES, chat
    args.output.mkdir(parents=True)
    (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    calls, rows, aborted = 0, [], False

    def bounded_chat(messages):
        nonlocal calls
        if calls >= 12:
            raise RuntimeError("MODEL_CALL_BUDGET_EXHAUSTED")
        calls += 1
        return chat(PROFILES["official"], messages, response_model=frozen_qa.AnswerOutput, max_output_tokens=1536)

    with (args.output / "records.jsonl").open("x", encoding="utf-8") as handle:
        for i, case in enumerate(cases):
            methods = [("before", before), ("after", frozen_qa)]
            if i % 2:
                methods.reverse()
            for name, module in methods:
                record = module.ask(case["question"], units, "proposed", bounded_chat)
                record.update(comparison_method=name, question_id=case["id"])
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append({"id": case["id"], "method": name, "outcome": record["final"]["outcome"],
                             "stop_reason": record["final"]["stop_reason"], "checks": record["checks"],
                             "usage": record["model"].get("usage"), "duration_ms": record["duration_ms"]})
                if record["final"]["stop_reason"] == "MODEL_ERROR":
                    aborted = True
                    break
            if aborted:
                break
    summary = {"purpose": plan["purpose"], "model_calls": calls, "aborted_on_first_model_error": aborted,
               "business_accuracy_measured": False, "rows": rows}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

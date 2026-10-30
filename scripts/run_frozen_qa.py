"""Ask the frozen Henan corpus one question, or run the paired evaluation.

Without --live nothing calls a model: ``ask`` shows the retrieved evidence and
``eval`` validates the questions-only file and prints the planned calls. With
--live, DeepSeek is used through the existing environment/proxy settings and a
hard --max-calls budget. Records, the blind A/B key and unit text stay in the
private runtime; the blind file for the reviewer and a count-only summary are
written to the repository.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp import frozen_qa  # noqa: E402


RUNTIME = ROOT / ".runtime/frozen-qa-20261030"
CORPUS = RUNTIME / "corpus.json"
BLIND_DIR = ROOT / "evaluation/independent-20261030/blind"
EVIDENCE = ROOT / "evidence/frozen-qa-20261030"


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def load_units(path=CORPUS):
    if not Path(path).is_file():
        raise SystemExit("Corpus missing: run scripts/build_frozen_corpus.py first")
    return json.loads(Path(path).read_text("utf-8"))["units"]


def live_chat(budget):
    from gov_mcp.deepseek import PROFILES, chat

    calls = {"count": 0}

    def call(messages):
        if calls["count"] >= budget:
            raise RuntimeError("MODEL_CALL_BUDGET_EXHAUSTED")
        calls["count"] += 1
        return chat(PROFILES["official"], messages, response_model=frozen_qa.AnswerOutput, max_output_tokens=1024)

    call.calls = calls
    return call


def offline_chat(messages):
    raise RuntimeError("DRY_RUN_NO_MODEL_CALL")


def ask(args):
    units = load_units()
    if not args.live:
        evidence = frozen_qa.retrieve(units, args.question + args.context)
        print(json.dumps({"dry_run": True, "gate": frozen_qa.scope_gate(args.question + args.context),
                          "evidence": [{k: item[k] for k in ("rank", "source_id", "locator", "score")}
                                       | {"text": item["text"][:200]} for item in evidence]},
                         ensure_ascii=False, indent=1))
        return
    record = frozen_qa.ask(args.question, units, args.method, live_chat(args.max_calls), context=args.context)
    out = RUNTIME / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({"record": str(out), **record["final"], "checks": record["checks"],
                      "usage": record["model"].get("usage")}, ensure_ascii=False, indent=1))


def evaluate(args):
    questions = frozen_qa.validate_questions(json.loads(Path(args.questions).read_text("utf-8")))
    if args.pilot:
        questions = questions[:args.pilot]
    planned = len(questions) * len(frozen_qa.METHODS)
    if not args.live:
        print(json.dumps({"dry_run": True, "questions": len(questions), "planned_model_calls_at_most": planned,
                          "questions_sha256": file_sha(args.questions), "seed": args.seed}, ensure_ascii=False))
        return
    if args.max_calls < planned:
        raise SystemExit(f"--max-calls {args.max_calls} is below the {planned} planned calls")
    units = load_units()
    chat = live_chat(args.max_calls)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = RUNTIME / "eval" / run_id
    folder.mkdir(parents=True)
    freeze = {"run_id": run_id, "questions_sha256": file_sha(args.questions), "corpus_sha256": file_sha(CORPUS),
              "frozen_qa_sha256": file_sha(ROOT / "src/gov_mcp/frozen_qa.py"), "seed": args.seed,
              "top_k": frozen_qa.TOP_K, "methods": list(frozen_qa.METHODS), "pilot": args.pilot or None,
              "model_profile": "deepseek official, temperature 0, non-thinking, 1024 output tokens"}
    (folder / "freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=1), "utf-8")
    records = []
    with (folder / "records.jsonl").open("w", encoding="utf-8") as handle:
        for item in questions:
            for method in frozen_qa.method_order(item["id"], args.seed):
                record = frozen_qa.ask(item["question"], units, method, chat, context=item.get("context", ""))
                record["question_id"] = item["id"]
                records.append(record)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
    items, key = frozen_qa.blind(records, args.seed)
    (folder / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), "utf-8")
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    (BLIND_DIR / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "items": items}, ensure_ascii=False,
                                                          indent=1) + "\n", "utf-8")
    summary = {"run_id": run_id, **{k: freeze[k] for k in ("questions_sha256", "corpus_sha256",
                                                             "frozen_qa_sha256", "seed", "pilot")},
               "model_calls": chat.calls["count"], "by_method": {}}
    for method in frozen_qa.METHODS:
        mine = [r for r in records if r["method"] == method]
        summary["by_method"][method] = {
            "answers": sum(r["final"]["outcome"] == "answer" for r in mine),
            "stops": sum(r["final"]["outcome"] == "stop" for r in mine),
            "model_errors": sum(r["final"]["stop_reason"] == "MODEL_ERROR" for r in mine),
            "checks_passed": sum(bool(r["checks"] and r["checks"]["passed"]) for r in mine),
            "input_tokens": sum((r["model"].get("usage") or {}).get("input_tokens", 0) for r in mine),
            "output_tokens": sum((r["model"].get("usage") or {}).get("output_tokens", 0) for r in mine)}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"eval-{run_id}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                         "utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("ask")
    one.add_argument("question")
    one.add_argument("--context", default="")
    one.add_argument("--method", choices=frozen_qa.METHODS, default="proposed")
    many = sub.add_parser("eval")
    many.add_argument("--questions", required=True, help="questions-only JSON list: id, question, optional context")
    many.add_argument("--pilot", type=int, default=0, help="Run only the first N questions")
    many.add_argument("--seed", default="20261030")
    for command in (one, many):
        command.add_argument("--live", action="store_true", help="Call the cloud model")
        command.add_argument("--max-calls", type=int, default=2)
    args = parser.parse_args()
    (ask if args.command == "ask" else evaluate)(args)


if __name__ == "__main__":
    main()

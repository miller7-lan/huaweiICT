"""Paired run of the single-shot proposed chain and the bounded flow v3, dry-run by default.

Input is a questions-only JSON list (id, question, optional context) or an
object with "cases". Without --live nothing calls a model: the plan and the
call ceiling are printed. With --live each question runs both methods in a
seeded order through the existing DeepSeek profile, with no retries and a hard
--max-calls budget (at most 4 per question: 1 single-shot + 3 flow).

Records with questions and answers stay in a new directory under .runtime.
The printed summary holds counts only. It measures engineering outcomes
(answers, partial answers, stops, calls, tokens); correctness needs frozen
references and human review.
"""
import argparse
import json
import re
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow  # noqa: E402

SOURCES = ("frozen_qa.py", "frozen_retrieval.py", "frozen_qa_flow_v3.py")


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / ".runtime/frozen-qa-20261030/corpus.json")
    parser.add_argument("--output", type=Path, required=True, help="New directory inside .runtime")
    parser.add_argument("--seed", default="20261114")
    parser.add_argument("--live", action="store_true", help="Call the cloud model")
    parser.add_argument("--max-calls", type=int, default=0)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()):
        parser.error("PRIVATE_RUNTIME_OUTPUT_REQUIRED")
    loaded = json.loads(args.questions.read_text("utf-8"))
    questions = frozen_qa.validate_questions(loaded["cases"] if isinstance(loaded, dict) else loaded)
    ceiling = len(questions) * (1 + flow.MAX_MODEL_CALLS)
    plan = {"questions": len(questions), "model_calls_at_most": ceiling, "retries": 0, "seed": args.seed,
            "questions_sha256": file_sha(args.questions),
            "source_sha256": {name: file_sha(ROOT / "src/gov_mcp" / name) for name in SOURCES}}
    if not args.live:
        print(json.dumps({"dry_run": True, "model_calls": 0, **plan}, ensure_ascii=False))
        return
    if args.max_calls < ceiling:
        parser.error(f"--max-calls {args.max_calls} is below the ceiling of {ceiling}")
    if args.output.exists():
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    units = json.loads(args.corpus.read_text("utf-8"))["units"]
    plan["corpus_sha256"] = file_sha(args.corpus)
    from gov_mcp.deepseek import PROFILES, chat

    used = {"count": 0}

    def bounded_chat(messages):
        if used["count"] >= args.max_calls:
            raise RuntimeError("MODEL_CALL_BUDGET_EXHAUSTED")
        used["count"] += 1
        return chat(PROFILES["official"], messages, response_model=frozen_qa.AnswerOutput, max_output_tokens=1024)

    args.output.mkdir(parents=True)
    (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    summary = {"single_shot": Counter(), "flow_v3": Counter()}
    with (args.output / "records.jsonl").open("w", encoding="utf-8") as handle:
        for item in questions:
            context = item.get("context", "")
            runs = {"single_shot": lambda: frozen_qa.ask(item["question"], units, "proposed", bounded_chat, context=context),
                    "flow_v3": lambda: flow.ask(item["question"], units, bounded_chat, context=context)}
            # Seeded per-question order, so neither method always runs first.
            for name in sorted(runs, key=lambda n: sha256(f"{args.seed}:{item['id']}:{n}".encode()).hexdigest()):
                before = used["count"]
                record = runs[name]()
                record.update(question_id=item["id"], compared_as=name)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                counts = summary[name]
                counts[record.get("result_kind") or record["final"]["outcome"]] += 1
                counts["model_calls"] += used["count"] - before
                if record["final"]["outcome"] == "stop":
                    counts["stop:" + (record.get("stop_origin") or record.get("gate") or record["final"]["stop_reason"])] += 1
                # Failure kinds of every draft, without claim numbers or values, show what to fix next.
                drafts = record["model"].get("calls") or [{"failures": (record.get("checks") or {}).get("failures", [])}]
                for code in (code for draft in drafts for code in draft.get("failures", [])):
                    counts["draft_failure:" + re.sub(r"^A\d+_", "", code).split(":")[0]] += 1
                if record.get("final_checks") and not record["final_checks"]["passed"]:
                    counts["final_not_verified"] += 1
                usages = [call.get("usage") for call in record["model"].get("calls", [record["model"]])]
                counts["input_tokens"] += sum((usage or {}).get("input_tokens", 0) for usage in usages)
                counts["output_tokens"] += sum((usage or {}).get("output_tokens", 0) for usage in usages)
    result = {"output": str(args.output), **plan, "model_calls": used["count"],
              "by_method": {name: dict(sorted(counts.items())) for name, counts in summary.items()}}
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

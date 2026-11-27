"""Closed-book LLM comparison arm: the same model answers with no retrieval and no corpus.

It stands for "ask a general chat model directly". The system prompt is a plain, fair
government-assistant prompt: answer from general knowledge, name the basis when possible,
and say so when unsure. No corpus unit, no scope gate and no verification are used, so the
output shows what the model says without the archive. Not part of the product; the frozen
src modules are only imported for the shared cloud client.

Records (question and answer text) go to a new directory under .runtime only.
"""
import argparse
import json
import sys
import time
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp import deepseek  # noqa: E402

METHOD = "closed_book"
SYSTEM = ("你是政务咨询助手。请根据你掌握的知识，用中文回答用户关于河南省居民身份证、户籍、居住证、"
          "出入境证件等办理与收费政策的问题。尽量写明依据（文件名称、条款或文号）。"
          "如果你无法确定，或问题需要以现行官方规定为准，请如实说明。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory inside .runtime")
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()):
        parser.error("PRIVATE_RUNTIME_OUTPUT_REQUIRED")
    loaded = json.loads(args.questions.read_text("utf-8"))
    questions = loaded["cases"] if isinstance(loaded, dict) else loaded
    config = deepseek.PROFILES["bailian"]
    plan = {"method": METHOD, "questions": len(questions), "model_calls_at_most": len(questions), "retries": 0,
            "questions_sha256": sha256(args.questions.read_bytes()).hexdigest(), "model": config,
            "max_output_tokens": args.max_output_tokens, "system_prompt": SYSTEM}
    if not args.live:
        print(json.dumps({"dry_run": True, **plan}, ensure_ascii=False, indent=1))
        return
    if args.output.exists():
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    args.output.mkdir(parents=True)
    (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), "utf-8")
    calls = 0
    with (args.output / "records.jsonl").open("w", encoding="utf-8") as out:
        for item in questions:
            started = time.monotonic()
            record = {"schema_version": 1, "method": METHOD, "question_id": item["id"], "question": item["question"]}
            try:
                calls += 1
                reply = deepseek.chat(config, [{"role": "system", "content": SYSTEM},
                                               {"role": "user", "content": item["question"]}],
                                      json_output=False, max_output_tokens=args.max_output_tokens)
                record["answer"] = reply["output"]
                record["usage"] = reply["usage"]
                record["served_model"] = reply["model"]
            except deepseek.CloudError as error:
                record["error"] = str(error)
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({"model_calls": calls, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

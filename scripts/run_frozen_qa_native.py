"""Ask the draft Nexent frozen-Q&A assistant one question through the runtime API.

One bounded run of the disabled, unpublished draft. The raw SSE log and final
answer stay in the private runtime. The public receipt contains status and counts.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp.json_output import object_from_json  # noqa: E402
from gov_mcp.frozen_qa import scope_gate  # noqa: E402
from nexent_api import NexentAPI  # noqa: E402
from provision_crossdoc_native_preview import read, write  # noqa: E402


RUNTIME = ROOT / ".runtime/frozen-qa-20261030"
OUTPUT = ROOT / "evidence/frozen-qa-20261030"
MAX_MODEL_STARTS = 8
EXPECTED_TOOLS = ("govkb_frozen_retrieve", "govkb_frozen_verify")
KB_TOOLS = ("knowledge_base_search", "govkb_frozen_verify_corpus")
VERIFY_FINAL = None
MAX_TOOL_SUBMISSIONS = 1


def summarize(events):
    tools = [(index, e.get("tool_name")) for index, e in enumerate(events) if e.get("type") == "tool"]
    finals = [(index, e.get("content")) for index, e in enumerate(events)
              if e.get("type") == "final_answer" and e.get("depth", 0) == 0]
    starts = [e for e in events if e.get("type") == "model_attempt_control" and e.get("phase") == "begin"]
    return {"tool_order": [name for _, name in tools], "model_starts": len(starts), "final_answers": len(finals),
            "final_answer": finals[-1][1] if finals else None, "events": len(events),
            "final_after_tools": bool(finals and tools and finals[-1][0] > tools[-1][0])}


def validate_run(summary, expected):
    final = summary["final_answer"]
    if summary["final_answers"] != 1 or not isinstance(final, str) or not final.strip():
        raise ValueError("NATIVE_FINAL_ANSWER_REQUIRED")
    tools = summary["tool_order"]
    # Some Nexent streams also report final_answer as a tool call. A top-level
    # final answer event after the business tools is the terminal signal in both formats.
    if tools and tools[-1] == "final_answer":
        tools = tools[:-1]
    first, repeat = expected
    if (not summary["final_after_tools"] or summary["model_starts"] < 1
            or not 2 <= len(tools) <= 1 + MAX_TOOL_SUBMISSIONS or tools[0] != first
            or any(tool != repeat for tool in tools[1:])):
        raise ValueError("NATIVE_TOOL_ORDER_MISMATCH")


def public_receipt(report):
    fields = ("passed", "error", "stop_unconfirmed", "duration_ms", "model_starts", "final_answers",
              "events", "recorded_at", "published", "agent_enabled", "final_matches_service")
    allowed = {*EXPECTED_TOOLS, *KB_TOOLS, "final_answer"}
    names = report.get("tool_order") or []
    return {**{key: report[key] for key in fields if key in report},
            "tool_order": [name for name in names if isinstance(name, str) and name in allowed],
            "other_tool_events": sum(not isinstance(name, str) or name not in allowed for name in names)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--knowledge-base", action="store_true",
                        help="Ask the knowledge-base draft (Nexent search + MCP corpus verify) instead")
    args = parser.parse_args()
    gate = scope_gate(args.question)
    if gate:
        print(json.dumps({"passed": False, "blocked": gate, "model_starts": 0}))
        return 2
    saved = read(RUNTIME / ("kb/native-drafts.json" if args.knowledge_base else "native-drafts.json"))
    prefix = "native-kb-run" if args.knowledge_base else "native-run"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    prior_tasks = set((RUNTIME / "flow-tasks").glob("*.json")) if VERIFY_FINAL else set()
    api = NexentAPI()
    events, conversation, report = [], None, {"question": args.question, "agent_id": saved["agents"]["assistant"]}
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "河南冻结资料问答"})["data"]["conversation_id"]
        request = {"query": args.question, "conversation_id": conversation, "agent_id": saved["agents"]["assistant"],
                   "model_id": saved["model_id"], "version_no": 0, "is_debug": True, "history": [],
                   "requested_output_tokens": 1024, "enable_plan": False, "enable_automation_tool": False}
        started = time.monotonic()
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=request,
                             headers={"Authorization": "Bearer " + api.token},
                             timeout=httpx.Timeout(120, connect=10)) as response:
            response.raise_for_status()
            size = 0
            with (RUNTIME / (stamp + "-sse.log")).open("x", encoding="utf-8") as log:
                for line in response.iter_lines():
                    log.write(line + "\n")
                    size += len(line.encode())
                    if size > 2 * 1024 ** 2 or time.monotonic() - started > 120:
                        raise ValueError("NATIVE_RUN_BOUND_EXCEEDED")
                    if line.startswith("data:") and line[5:].strip():
                        events.append(object_from_json(line[5:].strip()))
                        if sum(e.get("type") == "model_attempt_control" and e.get("phase") == "begin"
                               for e in events) > MAX_MODEL_STARTS:
                            raise ValueError("MODEL_START_LIMIT_EXCEEDED")
        report.update(duration_ms=round((time.monotonic() - started) * 1000, 1))
        summary = summarize(events)
        validate_run(summary, KB_TOOLS if args.knowledge_base else EXPECTED_TOOLS)
        if VERIFY_FINAL:
            VERIFY_FINAL(args.question, summary["final_answer"], prior_tasks)
            report["final_matches_service"] = True
        report["passed"] = True
    except Exception as error:
        error_code = str(error) if type(error) is ValueError and re.fullmatch(r"[A-Z_]+", str(error)) else type(error).__name__
        report.update(passed=False, error=error_code)
        if conversation is not None:
            try:
                api.runtime_call("GET", "/agent/stop/" + str(conversation))
            except Exception:
                report["stop_unconfirmed"] = True
    finally:
        api.close()
    report.update(summarize(events), recorded_at=datetime.now(timezone.utc).isoformat(),
                  conversation_id=conversation, published=False, agent_enabled=False)
    write(RUNTIME / f"{prefix}-{stamp}-private.json", report)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = public_receipt(report)
    write(OUTPUT / f"{prefix}-{stamp}.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())

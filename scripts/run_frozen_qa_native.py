"""Ask the draft Nexent frozen-Q&A assistant one question through the runtime API.

One debug run of the disabled, unpublished draft: bounded to 120 seconds, 2 MB
of events and eight model starts. The raw SSE log and a full private report (question,
final answer, usage events) stay in the private runtime directory. The evidence receipt
holds only whitelisted counts, tool order, hashes and statuses, so it can be published.

A run is judged in three steps, and ``passed`` means all three:
  transport_completed  the event stream was read to its end without an error or a bound
  protocol_valid       at least one model start and exactly one non-empty top-level final answer
  workflow_verified    the required tools (REQUIRED_TOOLS) were called, in that order
An empty or cut stream, a missing final answer or a missing tool is never a pass.
"""
import argparse
import json
import sys
import time
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp.json_output import object_from_json  # noqa: E402
from nexent_api import NexentAPI  # noqa: E402
from provision_crossdoc_native_preview import read, write  # noqa: E402


RUNTIME = ROOT / ".runtime/frozen-qa-20261030"
OUTPUT = ROOT / "evidence/frozen-qa-20261030"
MAX_MODEL_STARTS = 8
REQUIRED_TOOLS = ()  # tool names that must appear in this order; set by the flow's private copy
# The only fields an evidence receipt may hold.
PUBLIC_FIELDS = ("agent_id", "question_sha256", "final_answer_sha256", "final_answer_chars", "tool_order",
                 "model_starts", "final_answers", "events", "duration_ms", "transport_completed",
                 "protocol_valid", "workflow_verified", "passed", "failures", "error", "stop_unconfirmed",
                 "recorded_at", "conversation_id", "published", "agent_enabled", "private_report")


def in_order(seen, required):
    position = 0
    for name in seen:
        if position < len(required) and name == required[position]:
            position += 1
    return position == len(required)


def judge(report, required=None):
    """The three-step verdict described in the module docstring; updates and returns ``report``."""
    required = REQUIRED_TOOLS if required is None else required
    transport = bool(report.get("transport_completed"))
    final = report.get("final_answer")
    protocol_failures = []
    if report.get("model_starts", 0) < 1:
        protocol_failures.append("NO_MODEL_START")
    if report.get("final_answers") != 1 or not (isinstance(final, str) and final.strip()):
        protocol_failures.append("FINAL_ANSWER_MISSING_OR_NOT_UNIQUE")
    workflow_failures = [] if in_order(report.get("tool_order", []), list(required)) \
        else ["REQUIRED_TOOLS_MISSING_OR_OUT_OF_ORDER"]
    failures = ([] if transport else ["TRANSPORT_NOT_COMPLETED"]) + protocol_failures + workflow_failures
    protocol = transport and not protocol_failures
    report.update(protocol_valid=protocol, workflow_verified=protocol and not workflow_failures,
                  passed=not failures, failures=failures)
    return report


def public_receipt(report):
    """Whitelisted copy for evidence: no question, answer text or raw usage events."""
    final = report.get("final_answer")
    digest = {"question_sha256": sha256(report["question"].encode()).hexdigest() if report.get("question") else None,
              "final_answer_sha256": sha256(final.encode()).hexdigest() if isinstance(final, str) else None,
              "final_answer_chars": len(final) if isinstance(final, str) else 0}
    merged = {**report, **digest}
    return {name: merged[name] for name in PUBLIC_FIELDS if name in merged}


def summarize(events):
    tools = [e.get("tool_name") for e in events if e.get("type") == "tool"]
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and e.get("depth", 0) == 0]
    starts = [e for e in events if e.get("type") == "model_attempt_control" and e.get("phase") == "begin"]
    usage = [e for e in events if "token" in json.dumps(e).lower() and e.get("type") not in ("tool", "final_answer")]
    return {"tool_order": tools, "model_starts": len(starts), "final_answers": len(finals),
            "final_answer": finals[-1] if finals else None, "events": len(events),
            "usage_events": usage[-3:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--knowledge-base", action="store_true",
                        help="Ask the knowledge-base draft (Nexent search + MCP corpus verify) instead")
    args = parser.parse_args()
    saved = read(RUNTIME / ("kb/native-drafts.json" if args.knowledge_base else "native-drafts.json"))
    prefix = "native-kb-run" if args.knowledge_base else "native-run"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    api = NexentAPI()
    events, conversation, report = [], None, {"question": args.question, "agent_id": saved["agents"]["assistant"]}
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "河南冻结资料问答"})["data"]["conversation_id"]
        request = {"query": args.question, "conversation_id": conversation, "agent_id": saved["agents"]["assistant"],
                   "model_id": saved["model_id"], "version_no": 0, "is_debug": True, "history": [],
                   "requested_output_tokens": saved.get("requested_output_tokens", 1024), "enable_plan": False, "enable_automation_tool": False}
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
        report.update(duration_ms=round((time.monotonic() - started) * 1000, 1), transport_completed=True)
    except Exception as error:
        report.update(transport_completed=False, error=str(error)[:200])
        if conversation is not None:
            try:
                api.runtime_call("GET", "/agent/stop/" + str(conversation))
            except Exception:
                report["stop_unconfirmed"] = True
    finally:
        api.close()
    report.update(summarize(events), recorded_at=datetime.now(timezone.utc).isoformat(),
                  conversation_id=conversation, published=False, agent_enabled=False)
    judge(report)
    private = RUNTIME / f"{prefix}-{stamp}-report.json"
    write(private, report)  # full report with question and answer text: private runtime only
    report["private_report"] = str(private.relative_to(ROOT))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / f"{prefix}-{stamp}.json", public_receipt(report))
    print(json.dumps({k: report.get(k) for k in ("passed", "failures", "error", "tool_order", "model_starts",
                                                  "duration_ms", "final_answer")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

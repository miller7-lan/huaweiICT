"""Ask the draft Nexent frozen-Q&A assistant one question through the runtime API.

One debug run of the disabled, unpublished draft: bounded to 120 seconds, 2 MB
of events and eight model starts. The raw SSE log stays private; a summary with
the final answer, tool order and visible usage is written as evidence.
"""
import argparse
import json
import sys
import time
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
    args = parser.parse_args()
    saved = read(RUNTIME / "native-drafts.json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        report.update(duration_ms=round((time.monotonic() - started) * 1000, 1), passed=True)
    except Exception as error:
        report.update(passed=False, error=str(error)[:200])
        if conversation is not None:
            try:
                api.runtime_call("GET", "/agent/stop/" + str(conversation))
            except Exception:
                report["stop_unconfirmed"] = True
    finally:
        api.close()
    report.update(summarize(events), recorded_at=datetime.now(timezone.utc).isoformat(),
                  conversation_id=conversation, published=False, agent_enabled=False)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / f"native-run-{stamp}.json", report)
    print(json.dumps({k: report.get(k) for k in ("passed", "error", "tool_order", "model_starts", "duration_ms",
                                                  "final_answer")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

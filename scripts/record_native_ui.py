"""Validate and record user-operated native UI screenshots and real SSE traces."""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.acceptance import assess
from nexent_api import ROOT, RUNTIME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("developer", "citizen", "staff"), required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--benchmark", required=True, help="The evidence/native-* directory name")
    parser.add_argument("--operator", choices=("user", "agent"), default="user",
                        help="Record actual UI operator; agent use requires prior user authorization")
    args = parser.parse_args()
    summary_path = ROOT / "evidence" / args.benchmark / "summary.json"
    if summary_path.parent.parent != ROOT / "evidence" or not args.benchmark.startswith("native-"):
        raise ValueError("Benchmark must be an evidence/native-* directory")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not summary.get("core_chain_accepted"):
        raise ValueError("Complete the core benchmark before recording UI acceptance")
    screenshot = args.screenshot.read_bytes()
    if not (screenshot.startswith(b'\x89PNG\r\n\x1a\n') or screenshot.startswith(b'\xff\xd8\xff')):
        raise ValueError("A real PNG or JPEG screenshot is required")
    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    ephemeral_debug = (args.role == "developer" and trace.get("is_debug") is True
                       and trace.get("conversation_id") is None)
    expected_keys = {"conversation_id", "events", "is_debug"} if ephemeral_debug else {"conversation_id", "events"}
    if set(trace) != expected_keys or (not ephemeral_debug and (
            not isinstance(trace["conversation_id"], int) or isinstance(trace["conversation_id"], bool)
            or trace["conversation_id"] <= 0)):
        raise ValueError("Trace must identify a real conversation or an explicit ephemeral developer debug run")
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    principal = hashlib.sha256(f"{state['tenant_id']}\0{state['accounts'][args.role]['user_id']}".encode()).hexdigest()
    role = "staff" if args.role == "staff" else "citizen"
    finals = [e["content"] for e in trace["events"] if e.get("type") == "final_answer" and not e.get("depth", 0)]
    final = finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else json.loads("".join(finals))
    request_id = final["data"]["request_id"]
    audit = [json.loads(line) for line in (RUNTIME / "mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines() if line]
    matches = [i for i, row in enumerate(audit) if row.get("request_id") == request_id]
    if len(matches) != 1 or matches[0] < 1:
        raise ValueError("UI final answer is not correlated to the actual tool audit")
    actual = audit[matches[0]-1:matches[0]+1]
    assessment = assess(trace["events"], actual, role, principal,
                        child_id=state["agents"]["staff_assistant" if role == "staff" else "citizen_assistant"])
    if not assessment["passed"]:
        raise ValueError("UI trace failed: " + ", ".join(assessment["stop_reasons"]))
    if args.operator == "user" and input("确认已用该账号在原生界面完成本次操作、截图对应本会话。输入 VERIFIED：").strip() != "VERIFIED":
        raise SystemExit("UI acceptance was not confirmed; no receipt saved.")
    dest = ROOT / "evidence/native-ui" / args.benchmark
    dest.mkdir(parents=True, exist_ok=True)
    image_file = dest / (args.role + args.screenshot.suffix.lower())
    trace_file = dest / (args.role + "-trace.json")
    if args.screenshot.resolve() != image_file.resolve():
        shutil.copyfile(args.screenshot, image_file)
    trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    path = ROOT / "evidence/native-ui.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if report.get("benchmark_reference") != args.benchmark:
        report = {"benchmark_reference": args.benchmark, "records": {}}
    report["records"][args.role] = {"passed": True, "source": args.operator + "-operated native UI",
        "captured_at": datetime.now(timezone.utc).isoformat(), "conversation_id": trace["conversation_id"],
        "screenshot": image_file.relative_to(ROOT).as_posix(), "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        "trace": trace_file.relative_to(ROOT).as_posix(), "trace_sha256": hashlib.sha256(trace_file.read_bytes()).hexdigest()}
    if ephemeral_debug:
        report["records"][args.role].update(run_mode="debug", debug_request_id=request_id)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.role, "UI evidence recorded.")


if __name__ == "__main__":
    main()

"""Archive/assess a completed native UI trace against actual govx tool audits."""

import argparse
import hashlib
import json
from pathlib import Path

from m1_assessment import assess_m1
from verify_native_faults import root_final
from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation", type=int, required=True)
    parser.add_argument("--screenshot", default="output/playwright/m1-BT03.png")
    parser.add_argument("--trace-dir", type=Path, default=RUNTIME / "m1/ui-traces")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/m1-native-ui-citizen.json")
    args = parser.parse_args()
    trace_dir = args.trace_dir.resolve()
    output_path = args.output.resolve()
    if not trace_dir.is_relative_to(RUNTIME / "m1") or not output_path.is_relative_to(ROOT / "evidence"):
        raise ValueError("Private M1 trace directory and public evidence output required")
    trace_path = trace_dir / f"conversation-{args.conversation}.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["conversation_id"] == args.conversation
    screenshot = (ROOT / args.screenshot).resolve()
    if not screenshot.is_relative_to(ROOT / "output/playwright") or not screenshot.is_file():
        raise ValueError("Actual screenshot required within output/playwright")
    api = NexentAPI()
    try:
        user = api.login(json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))["fresh_citizen"])
    finally:
        api.close()
    principal = hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
    final = root_final(trace["events"])
    entries = [json.loads(line) for line in (RUNTIME / "m1/mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    entries = [a for a in entries if a["principal_sha256"] == principal]
    last = next(i for i, a in enumerate(entries) if a["request_id"] == final["data"]["request_id"])
    audit = entries[max(0, last-1):last+1]
    state = json.loads((RUNTIME / "m1/nexent-state.json").read_text(encoding="utf-8"))
    assessment = assess_m1(trace["events"], audit, "citizen", principal, state["agents"]["govx_citizen_assistant"])
    record = {"source": "agent-operated native UI", "conversation_id": args.conversation,
        "screenshot": args.screenshot, "screenshot_sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(), "events": trace["events"],
        "audit": audit, "principal_sha256": principal, **assessment}
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("M1 native UI trace", "PASS" if record["passed"] else "FAIL", record["stop_reasons"])
    raise SystemExit(0 if record["passed"] else 1)


if __name__ == "__main__":
    main()

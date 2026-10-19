"""One explicitly enabled native Qwen run; maximum two attempts in this activity."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle
from gov_mcp.matter_native import MatterNative, SERVICE, SCENARIO
from nexent_api import NexentAPI
from provision_matter_native import first_action, protocol_text, read, write, signatures, check_draft
from verify_regional_native_preview import external_failure


def assess(events, preview, run_id):
    errors = []
    tools = [e.get("tool_name") for e in events if e.get("type") == "tool"]
    required = {"read_skill_md", "govmatter_open", "govmatter_retrieve", "govmatter_verify", "govmatter_state", "govmatter_finalize"}
    if not required <= set(tools) or any(tools.count(t) != 1 for t in required): errors.append("EXACT_TOOL_TRACE_REQUIRED")
    child_tools = {e.get("tool_name") for e in events if e.get("type") == "tool" and e.get("depth", 0)}
    if not {"govmatter_retrieve", "govmatter_verify"} <= child_tools or sum(e.get("type") == "subagent_start" for e in events) < 2:
        errors.append("ACTUAL_CHILD_TRACE_REQUIRED")
    if any(e.get("type") in ("error", "max_steps_reached") for e in events): errors.append("NATIVE_RUNTIME_ERROR")
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
    answer = None
    try:
        if len(finals) != 1: raise ValueError()
        answer = json.loads(finals[0]) if isinstance(finals[0], str) else finals[0]
        task = preview.task(answer["task_id"])
        if task["run_id"] != run_id or task["status"] != "complete" or answer != task["answer"] or preview.finalize(task["task_id"]) != answer:
            errors.append("PERSISTENT_FINAL_RESULT_MISMATCH")
        if (answer["mode"] != SERVICE or answer["scenario_id"] != SCENARIO
            or answer["knowledge_published"] is not False or answer["formal_business_answer"] is not False
            or answer["human_review"] != "pending" or answer["verification"]["jev_called"] is not False
            or not answer["proofs"] or any(len(p["source_ids"]) < 2 for p in answer["proofs"])):
            errors.append("CANDIDATE_PROOF_BOUNDARY_FAILED")
    except (ValueError, KeyError, TypeError, IndexError): errors.append("STRUCTURED_RESULT_REQUIRED")
    return {"passed": not errors, "stop_reasons": errors, "actual_tools": tools, "answer": answer if not errors else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repair-note")
    args = parser.parse_args()
    if not args.live: parser.error("--live required; no automatic model calls")
    access = read(args.runtime / "access.json")
    preview = MatterNative(args.runtime, MatterLifecycle(access["lifecycle_root"]))
    profile = preview.profile
    saved = read(args.runtime / "native-drafts.json")
    events, api, run_id = [], NexentAPI(), None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": SERVICE,
        "models": {"controller": "Qwen3-8B", "retriever": "Qwen3-4B", "verifier": "Qwen3-4B"},
        "native_run_requests": 0, "cloud_model_calls": 0, "jev_calls": 0,
        "human_business_review": False, "independent_evaluation": False, "knowledge_published": False}
    report.update(protocol=profile["protocol"], native_run_limit=profile["run_limit"],
        run_seconds_limit=profile["run_seconds"], expected_main_code_actions=1 if profile["protocol"] == "dataflow-v4" else 2 if profile["protocol"] != "legacy" else None)
    start = time.perf_counter(); failure = None
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            health = client.get(f"http://127.0.0.1:{profile['port']}/health"); health.raise_for_status()
            expected = {name: hashlib.sha256((ROOT / "src/gov_mcp" / name).read_bytes()).hexdigest()
                for name in ("matter_native.py", "matter_native_server.py", "matter_lifecycle.py", "matter_graph.py", "matter_guidance.py")}
            if health.json().get("startup_source_sha256") != expected:
                raise RuntimeError("SERVER_SOURCE_FINGERPRINT_MISMATCH")
            report["startup_source_sha256"] = expected
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        if signatures(api, saved["tenant_id"], saved["agents"].values()) != saved["existing_signatures"]:
            raise RuntimeError("EXISTING_AGENTS_CHANGED")
        for role in saved["agents"]: check_draft(api, role, saved)
        run_id = preview.begin(args.repair_note)
        report["run_id"], report["native_run_requests"] = run_id, 1
        query = first_action(profile) + "\n/no_think"
        if profile["protocol"] == "legacy": query = protocol_text(profile) + "\nStart with this exact action, then use actual returned IDs:\n" + query
        body = {"query": query,
            "agent_id": saved["agents"]["controller"], "model_id": saved["model_ids"]["main"],
            "version_no": 0, "is_debug": True, "history": [], "requested_output_tokens": profile["main_output_tokens"],
            "enable_plan": False, "enable_automation_tool": False}
        with (args.runtime / (stamp + "-events.jsonl")).open("x", encoding="utf-8") as log:
            with api.http.stream("POST", api.runtime_base + "/agent/run", json=body,
                headers={"Authorization": "Bearer " + api.token, "x-user-timezone": "Asia/Shanghai"},
                timeout=httpx.Timeout(connect=10, read=90, write=10, pool=10)) as response:
                response.raise_for_status(); size = 0
                report["runtime_run_id"] = response.headers.get("run_id")
                for line in response.iter_lines():
                    size += len(line.encode())
                    if size > 2 * 1024**2 or time.perf_counter() - start > profile["run_seconds"]:
                        raise RuntimeError("NATIVE_RUN_BOUND_EXCEEDED")
                    if line.startswith("data:") and line[5:].strip():
                        event = json.loads(line[5:].strip())
                        if isinstance(event, dict):
                            events.append(event); log.write(json.dumps(event, ensure_ascii=False) + "\n"); log.flush()
        report.update(assess(events, preview, run_id))
        failure = external_failure(events)
        if failure: report.update(passed=False, external_failure=failure)
    except Exception as error:
        failure = external_failure(events, error)
        report.update(passed=False, error_type=type(error).__name__, external_failure=failure)
        if isinstance(error, RuntimeError) and str(error) == "NATIVE_RUN_BOUND_EXCEEDED":
            report["stop_reasons"] = ["NATIVE_RUN_BOUND_EXCEEDED"]
    finally:
        if run_id and not report.get("passed") and re.fullmatch(r"debug-[0-9a-f]{32}", report.get("runtime_run_id") or ""):
            try:
                stop = api.runtime_call("GET", "/agent/stop/" + report["runtime_run_id"])
                report["owned_runtime_stop"] = {"status": stop.get("status"), "already_stopped": stop.get("already_stopped", False)}
            except Exception as stop_error:
                report["owned_runtime_stop_error_type"] = type(stop_error).__name__
        api.close()
        if run_id: preview.finish(run_id, passed=report.get("passed", False), external_failure=bool(failure))
        report.update(duration_ms=round((time.perf_counter() - start) * 1000, 2),
            event_types=dict(Counter(e.get("type") for e in events)), campaign=preview.store.get("state", "campaign"))
        report["actual_tools"] = [e.get("tool_name") for e in events if e.get("type") == "tool"]
        report["model_attempt_begins"] = sum(e.get("type") == "model_attempt_control" and e.get("phase") == "begin" for e in events)
        report["model_attempt_rollbacks"] = sum(e.get("type") == "model_attempt_control" and e.get("phase") == "rollback" for e in events)
        report["main_code_actions"] = sum(e.get("type") == "parse" and not e.get("depth", 0) for e in events)
        report["code_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("scripts/verify_matter_native.py", "scripts/provision_matter_native.py", "config/skills/" + profile["skill"] + "/SKILL.md")}
        write(args.runtime / (stamp + "-report.json"), report)
        folder = {"legacy": "native-qwen", "batched-close-v2": "native-batched-v2", "first-only-v3": "native-first-v3", "dataflow-v4": "native-dataflow-v4"}[profile["protocol"]]
        output = ROOT / "evidence/real-matter-closure" / folder / (stamp + ".json")
        output.parent.mkdir(exist_ok=True, parents=True); write(output, report)
    print(json.dumps({k: report.get(k) for k in ("passed", "stop_reasons", "external_failure", "error_type", "duration_ms", "event_types", "actual_tools")}, ensure_ascii=False))
    print(json.dumps({"evidence": output.relative_to(ROOT).as_posix(), "native_run_requests": report["native_run_requests"]}))
    return 0 if report.get("passed") else 1


if __name__ == "__main__": raise SystemExit(main())

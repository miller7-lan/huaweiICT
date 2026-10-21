"""Single reserved native cross-document run; without --live write only a plan."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.json_output import object_from_json
from nexent_api import NexentAPI
from provision_crossdoc_native_preview import (NAMES, SCENARIO, TOOLS, PROTOCOL, SKILL, first_action,
    check_drafts, check_service, check_skill, original_signatures, read, write, fingerprint, source_digest)
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/crossdoc-native-preview-20261019"
OUTPUT = ROOT / "evidence/crossdoc-native-preview-20261019"
SOURCE_PATHS = ("src/gov_mcp/crossdoc_preview.py", "src/gov_mcp/crossdoc_preview_server.py",
                "src/gov_mcp/crossdoc_reasoning.py", "scripts/provision_crossdoc_native_preview.py",
                "scripts/verify_crossdoc_native_preview.py", "config/skills/govkb-crossdoc-preview/SKILL.md")
STAGES = ("read_skill_md", "govkb_crossdoc_open_query", "govkb_crossdoc_retrieve",
          "govkb_crossdoc_verify", "govkb_crossdoc_state", "govkb_crossdoc_finalize")


def external_failure(events, error=None):
    codes = {401: "AUTHENTICATION_FAILED", 402: "PAYMENT_REQUIRED", 403: "ACCESS_DENIED",
             429: "RATE_LIMITED", 451: "ACCESS_RESTRICTED"}
    if isinstance(error, httpx.HTTPStatusError):
        return codes.get(error.response.status_code, "UPSTREAM_HTTP_ERROR")
    if isinstance(error, httpx.RequestError):
        return "TRANSPORT_UNAVAILABLE"
    if isinstance(error, RuntimeError):
        match = re.search(r"HTTP (401|402|403|429|451)\b", str(error))
        if match:
            return codes[int(match[1])]
    markers = {"AUTHENTICATION_FAILED": ("authentication_failed", "invalid api key", "incorrect api key"),
               "PAYMENT_REQUIRED": ("payment_required", "insufficient balance", "insufficient_quota"),
               "RATE_LIMITED": ("rate_limit", "http 429"),
               "ACCESS_DENIED": ("access_denied", "http 403"),
               "ACCESS_RESTRICTED": ("access_restricted", "http 451"),
               "TRANSPORT_UNAVAILABLE": ("transport_unavailable", "connection error", "connection timeout")}
    for event in events:
        if event.get("type") == "error":
            content = str(event.get("content", "")).lower()
            for code, matches in markers.items():
                if any(marker in content for marker in matches):
                    return code
    return None


def assess(events, preview, run_id):
    """Require actual ordered tool execution and the exact persisted program result."""
    errors = []
    calls = [(index, event) for index, event in enumerate(events) if event.get("type") == "tool"]
    actual = [event.get("tool_name") for _, event in calls]
    allowed = set(STAGES) | {NAMES["retriever"], NAMES["verifier"], "final_answer"}
    if any(name not in allowed for name in actual):
        errors.append("UNEXPECTED_TOOL_EXECUTED")
    if not set(STAGES) <= set(actual):
        errors.append("ACTUAL_TOOL_TRACE_MISSING")
    if any(actual.count(name) != 1 for name in STAGES):
        errors.append("TOOL_STAGE_COUNT_MISMATCH")
    stage_calls = [(index, event) for index, event in calls if event.get("tool_name") in STAGES]
    if [event.get("tool_name") for _, event in stage_calls] != list(STAGES):
        errors.append("TOOL_STAGE_ORDER_MISMATCH")
    if any(event.get("depth", 0) != (1 if event.get("tool_name") in {
            "govkb_crossdoc_retrieve", "govkb_crossdoc_verify"} else 0) for _, event in stage_calls):
        errors.append("TOOL_STAGE_DEPTH_MISMATCH")
    if sum(event.get("type") == "subagent_start" for event in events) != 2:
        errors.append("ACTUAL_CHILD_TRACE_MISSING")
    if any(actual.count(name) > 1 for name in (NAMES["retriever"], NAMES["verifier"])):
        errors.append("CHILD_STAGE_COUNT_MISMATCH")
    if any(event.get("type") in {"error", "max_steps_reached"} for event in events):
        errors.append("NATIVE_RUNTIME_ERROR")
    finals = [(index, event.get("content")) for index, event in enumerate(events)
              if event.get("type") == "final_answer" and not event.get("depth", 0)]
    answer = None
    try:
        if len(finals) != 1:
            raise ValueError("EXACT_ONE_ROOT_FINAL_REQUIRED")
        answer = object_from_json(finals[0][1]) if isinstance(finals[0][1], str) else finals[0][1]
        if not isinstance(answer, dict):
            raise ValueError("JSON_OBJECT_REQUIRED")
        finalize_indices = [index for index, event in calls if event.get("tool_name") == "govkb_crossdoc_finalize"]
        if len(finalize_indices) != 1 or finals[0][0] <= finalize_indices[0]:
            errors.append("FINAL_ANSWER_ORDER_MISMATCH")
        task = preview.get_task(answer["task_id"])
        if (task["run_id"] != run_id or task["status"] != "complete"
                or task["answer"] != answer or preview.finalize(answer["task_id"]) != answer):
            errors.append("PERSISTED_RESULT_MISMATCH")
        context = preview.preflight()
        reasoning = answer["reasoning"]
        trace = reasoning["trace"]
        seen = set()
        for index, hop in enumerate(trace):
            identifier, dependencies = hop["id"], hop["depends_on"]
            if (not isinstance(identifier, str) or identifier in seen or not hop["evidence"]
                    or not isinstance(dependencies, list) or not set(dependencies) <= seen
                    or (index == 0 and dependencies) or (index > 0 and not dependencies)):
                raise ValueError("DEPENDENT_EVIDENCE_CHAIN_REQUIRED")
            seen.add(identifier)
        if (answer.get("mode") != "crossdoc_archived_preview" or answer.get("published") is not False
                or answer.get("formal_approval") is not False
                or answer.get("verification_method") != "deterministic_source_bound"
                or answer.get("source_binding") != context["binding"]
                or reasoning.get("status") != "derived"
                or reasoning.get("conclusion") != "cannot_establish_full_cycle_coverage"
                or len(trace) != 3 or context.get("document_count") != 2):
            errors.append("CROSSDOC_RESULT_CONTRACT_FAILED")
        counts = preview.counts()
        if counts.get("jev_calls") != 0 or counts.get("translation_calls") != 0 or counts.get("native_runs") != 1:
            errors.append("CAMPAIGN_BUDGET_CONTRACT_FAILED")
    except (ValueError, KeyError, TypeError, IndexError, OSError, RuntimeError):
        errors.append("STRUCTURED_PROGRAM_RESULT_MISSING")
    errors = list(dict.fromkeys(errors))
    return {"passed": not errors, "stop_reasons": errors, "actual_tools": actual,
            "answer": answer if not errors else None,
            "answer_sha256": fingerprint(answer) if answer is not None else None}


def current_sources():
    return {path: source_digest((ROOT / path).read_bytes()) for path in SOURCE_PATHS}


def trace_summary(events):
    tools = [{"event_index": index, "name": event.get("tool_name"), "depth": event.get("depth", 0)}
             for index, event in enumerate(events) if event.get("type") == "tool"]
    usage = []
    for event in events:
        if event.get("type") == "token_count":
            try:
                value = object_from_json(event["content"]) if isinstance(event.get("content"), str) else event["content"]
                usage.append({key: value.get(key) for key in ("step_number", "step_input_tokens", "step_output_tokens",
                    "output_finish_reason", "compression_calls")} | {"depth": event.get("depth", 0)})
            except (ValueError, KeyError, TypeError):
                usage.append({"status": "unreadable_usage_event", "depth": event.get("depth", 0)})
    return {"tools": tools, "child_starts": sum(event.get("type") == "subagent_start" for event in events),
            "observed_usage_steps": usage,
            "usage_scope": "Observed native token_count events; child model usage may be missing, not total model cost"}


def plan():
    return {"mode": "crossdoc_archived_preview", "scenario_id": SCENARIO, "native_run_budget": 1,
            "native_run_requests_this_plan": 0, "inference_calls_this_plan": 0,
            "jev_calls": 0, "translation_calls": 0, "automatic_retry": False,
            "published": False, "formal_approval": False, "live_requires": "--live",
            "scope": "Native controller and two children execute archived public deterministic three-hop reasoning; current business applicability stays paused."}


def live_run(preview, api, saved, *, clock=time.perf_counter):
    """No retries. A reservation remains spent even when HTTP or assessment fails."""
    events = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    journal = RUNTIME / (stamp + "-request.json")
    event_path = RUNTIME / (stamp + "-events.json")
    raw_stream_path = RUNTIME / (stamp + "-sse.log")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": "crossdoc_archived_preview",
              "scenario_id": SCENARIO, "published": False, "formal_approval": False,
              "native_run_requests": 0, "native_run_budget": 1, "automatic_retry": False,
              "jev_calls": 0, "translation_calls": 0, "cost_amount": None, "passed": False}
    run_id, failure = None, None
    start = clock()
    try:
        context = preview.preflight()
        health = check_service()
        if original_signatures(api) != saved["original_signatures"]:
            raise RuntimeError("LEGACY_AGENT_STATE_CHANGED")
        check_drafts(api, saved)
        check_skill(api, saved)
        report.update(source_binding=context["binding"], service_startup_source_sha256=health["startup_source_sha256"],
                      source_sha256=current_sources())
        run_id = preview.begin_run()
        report["run_id"] = run_id
        body = {"query": PROTOCOL + "\nStart with this exact action, then use the actual returned task_id:\n" + first_action(),
                "agent_id": saved["agents"]["controller"], "model_id": saved["model_id"], "version_no": 0,
                "is_debug": True, "history": [], "requested_output_tokens": 1024,
                "enable_plan": False, "enable_automation_tool": False}
        # The campaign reservation and complete non-secret request survive a transport failure.
        write(journal, {"run_id": run_id, "source_binding": context["binding"], "body": body,
                        "request_sha256": fingerprint(body), "source_sha256": report["source_sha256"]})
        write(event_path, events)
        raw_stream_path.write_text("", encoding="utf-8")
        report.update(native_run_requests=1, request_sha256=fingerprint(body))
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=body,
                             headers={"Authorization": "Bearer " + api.token, "x-user-timezone": "Asia/Shanghai"}) as response:
            response.raise_for_status()
            size = 0
            for line in response.iter_lines():
                # Keep malformed/partial provider events too, in the private directory.
                with raw_stream_path.open("a", encoding="utf-8") as raw_stream:
                    raw_stream.write(line + "\n")
                size += len(line.encode())
                if size > 2 * 1024**2 or clock() - start > 120:
                    raise RuntimeError("NATIVE_RUN_BOUND_EXCEEDED")
                if line.startswith("data:") and line[5:].strip():
                    event = object_from_json(line[5:].strip())
                    events.append(event)
                    # Persist each received event before processing the next line.
                    write(event_path, events)
        report.update(assess(events, preview, run_id))
        if report.get("answer") and report["answer"].get("source_binding") != report["source_binding"]:
            report.update(passed=False, stop_reasons=[*report.get("stop_reasons", []), "SOURCE_CHANGED_DURING_RUN"])
        if original_signatures(api) != saved["original_signatures"]:
            report.update(passed=False, stop_reasons=[*report.get("stop_reasons", []), "LEGACY_AGENTS_CHANGED"])
        check_drafts(api, saved)
        if current_sources() != report["source_sha256"]:
            report.update(passed=False, stop_reasons=[*report.get("stop_reasons", []), "EXECUTION_SOURCE_CHANGED"])
    except Exception as error:
        failure = error
        code = external_failure(events, error)
        if not code:
            candidate = str(error)
            code = candidate if re.fullmatch(r"[A-Z_]+", candidate) else "NATIVE_VERIFICATION_UNAVAILABLE"
        report.update(passed=False, error_type=type(error).__name__, error_code=code)
    finally:
        write(event_path, events)
        external = external_failure(events, failure)
        if external:
            report.update(passed=False, error_code=external)
        if run_id:
            reason = report.get("error_code") or (",".join(report.get("stop_reasons", [])) if not report.get("passed") else None)
            preview.end_run(run_id, bool(report.get("passed")), reason)
    report.update(duration_ms=round((clock() - start) * 1000, 2), campaign=preview.counts(),
                  event_types=dict(Counter(event.get("type") for event in events)),
                  events_sha256=source_digest(event_path.read_bytes()),
                  source_sha256_after=current_sources(), trace=trace_summary(events))
    if journal.exists():
        report["request_record_sha256"] = source_digest(journal.read_bytes())
    if raw_stream_path.exists():
        report["raw_stream_sha256"] = source_digest(raw_stream_path.read_bytes())
    write(OUTPUT / (stamp + ".json"), report)
    if run_id:
        write(OUTPUT / "latest.json", {"evidence": stamp + ".json", "private_events": event_path.name,
                                      "run_id": run_id, "passed": report["passed"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Spend the single explicitly authorized native run; no retries")
    mode.add_argument("--offline", action="store_true", help="Reassess saved trace only; no network or models")
    args = parser.parse_args(argv)
    if not args.live and not args.offline:
        result = plan()
        write(OUTPUT / "native-run-plan.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    from gov_mcp.crossdoc_preview import CrossdocPreview
    preview = CrossdocPreview(RUNTIME)
    if args.offline:
        latest = read(OUTPUT / "latest.json")
        result = assess(read(RUNTIME / latest["private_events"]), preview, latest["run_id"])
        result.update(evidence_source=latest["evidence"], model_calls=0, campaign=preview.counts(), source_sha256=current_sources())
        write(OUTPUT / "offline-assessment.json", result)
        print(json.dumps({key: result[key] for key in ("passed", "stop_reasons", "model_calls", "campaign")}))
        return 0 if result["passed"] else 1
    ensure_private_directory(RUNTIME)
    saved = read(RUNTIME / "native-drafts.json")
    api = NexentAPI()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        result = live_run(preview, api, saved)
    finally:
        api.close()
    print(json.dumps({key: result.get(key) for key in ("passed", "stop_reasons", "error_code", "duration_ms", "campaign")}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

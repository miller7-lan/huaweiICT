"""One explicitly enabled real native preview run; durable maximum three per campaign."""
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
from gov_mcp.regional_preview import RegionalPreview as Preview, SCENARIOS, PAUSES, scenario, PreviewError, read, write, source_digest, fingerprint
from gov_mcp.json_output import exact_json_equal, object_from_json
from nexent_api import NexentAPI
from provision_regional_native_preview import NAMES, first_action, PROTOCOL, original_signatures, check_owned_draft

RUNTIME = ROOT / ".runtime/regional-native-preview"


def external_failure(events, error=None):
    codes = {401: 'AUTHENTICATION_FAILED', 402: 'PAYMENT_REQUIRED', 403: 'ACCESS_DENIED',
             429: 'RATE_LIMITED', 451: 'ACCESS_RESTRICTED'}
    if isinstance(error, httpx.HTTPStatusError):
        return codes.get(error.response.status_code, 'UPSTREAM_HTTP_ERROR')
    if isinstance(error, httpx.RequestError):
        return 'TRANSPORT_UNAVAILABLE'
    if isinstance(error, RuntimeError):
        found = re.search(r'HTTP (401|402|403|429|451)\b', str(error))
        if found:
            return codes[int(found[1])]
    markers = {'AUTHENTICATION_FAILED': ('authentication_failed', 'invalid api key', 'incorrect api key'),
               'PAYMENT_REQUIRED': ('payment_required', 'insufficient balance', 'insufficient_quota'),
               'ACCESS_RESTRICTED': ('access_restricted', 'http 451'),
               'ACCESS_DENIED': ('access_denied', 'http 403'),
               'RATE_LIMITED': ('rate_limited', 'rate_limit', 'http 429'),
               'TRANSPORT_UNAVAILABLE': ('transport_unavailable', 'connection error', 'connection timeout')}
    for event in events:
        if event.get('type') == 'error':
            value = str(event.get('content', '')).lower()
            for code, matches in markers.items():
                if any(marker in value for marker in matches):
                    return code
    return None


def assess(events, preview, run_id):
    errors = []
    actual = [e.get("tool_name") for e in events if e.get("type") == "tool"]
    required = {"read_skill_md", "govkb_regional_open_query", "govkb_regional_retrieve", "govkb_regional_verify", "govkb_regional_state", "govkb_regional_finalize"}
    if not required <= set(actual):
        errors.append("ACTUAL_TOOL_TRACE_MISSING")
    if any(actual.count(name) != 1 for name in required):
        errors.append("TOOL_STAGE_COUNT_MISMATCH")
    children = {e.get("tool_name") for e in events if e.get("type") == "tool" and e.get("depth", 0)}
    if not {"govkb_regional_retrieve", "govkb_regional_verify"} <= children or sum(e.get("type") == "subagent_start" for e in events) < 2:
        errors.append("ACTUAL_CHILD_TRACE_MISSING")
    if any(e.get("type") in {"error", "max_steps_reached"} for e in events):
        errors.append("NATIVE_RUNTIME_ERROR")
    finals = [e.get("content") for e in events if e.get("type") == "final_answer" and not e.get("depth", 0)]
    answer = None
    try:
        if len(finals) != 1:
            raise ValueError()
        answer = object_from_json(finals[0]) if isinstance(finals[0], str) else finals[0]
        task = preview.get_task(answer["task_id"])
        if (task["run_id"] != run_id or task["scenario_id"] != answer["scenario_id"] or task["status"] != "complete"
                or not exact_json_equal(task["answer"], answer)
                or not exact_json_equal(preview.finalize(task["task_id"]), answer)):
            errors.append("PERSISTED_RESULT_MISMATCH")
        identifier = answer["scenario_id"]
        spec = scenario(identifier)
        if (answer["mode"] != "regional_public_preview" or answer["published"] is not False or answer["formal_approval"] is not False
                or not any(f["status"] == "source_supported" and f["citations"] for f in answer["source_facts"])
                or not set(PAUSES[identifier]) <= {p["field"] for p in answer["pending_business_conclusions"]}
                or any(p["status"] != "paused" for p in answer["pending_business_conclusions"])
                or answer["unknown_fields"] != spec["unknown"]
                or answer["unsupported_assertion"]["accepted_as_business_fact"] is not False
                or answer["source_context"]["source_id"] != spec["source_id"]
                or any(c["source_id"] != spec["source_id"] for f in answer["source_facts"] for c in f["citations"])):
            errors.append("PARTIAL_PREVIEW_CONTRACT_FAILED")
        record = read(preview.root / (task["task_id"] + "-jev.json"))
        receipts = record["jev_receipts"]
        if (record.get("test_fixture") is True or len(receipts) != 1 or receipts[0].get("http_status") != 200 or receipts[0].get("status") != "ok"
                or receipts[0].get("served_model") != "jev-1.13.0" or not record["result"].get("english_input")
                or len(record["translation_receipts"]) != 1 or record["translation_receipts"][0].get("translation_calls") != 1):
            errors.append("ACTUAL_JEV_TRANSLATION_RECEIPT_MISSING")
    except (ValueError, KeyError, TypeError, IndexError, OSError, RecursionError):
        errors.append("STRUCTURED_PROGRAM_RESULT_MISSING")
    return {"passed": not errors, "stop_reasons": errors, "actual_tools": actual, "answer": answer if not errors else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Reassess latest saved trace; never starts a native run or model request")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--repair-note", help="Required for retry; identify the concrete implemented repair")
    args = parser.parse_args()
    if args.live == args.offline:
        parser.error("Choose --live or --offline; no automatic cloud calls")
    preview = Preview(RUNTIME)
    if args.offline:
        out = ROOT / 'evidence/regional-native-preview'
        reports = [(p, read(p)) for p in sorted(out.glob('20*.json'))]
        matches = [(p, r) for p, r in reports if r.get('scenario_id') == args.scenario]
        if not matches:
            parser.error('No saved run for this scenario')
        path, report = matches[-1]
        events = read(RUNTIME / (path.stem + '-events.json'))
        result = assess(events, preview, report['run_id'])
        result.update(scenario_id=args.scenario, evidence_source=path.name, model_calls=0,
                      code_sha256=source_digest(Path(__file__).read_bytes()), campaign=preview.counts())
        write(out / (args.scenario + '-offline-assessment.json'), result)
        print(json.dumps({k: result[k] for k in ('passed', 'stop_reasons', 'scenario_id', 'model_calls', 'campaign')}))
        return 0 if result['passed'] else 1
    saved = read(RUNTIME / "native-drafts.json")
    events, api = [], NexentAPI()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": "regional_public_preview",
              "published": False, "model_training": False, "native_run_requests": 0, "scenario_id": args.scenario}
    run_id = None
    failure = None
    start = time.perf_counter()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        if saved.get('example_scenario') != args.scenario:
            raise PreviewError('SCENARIO_CODE_EXAMPLE_REQUIRED')
        if original_signatures(api) != saved["original_signatures"]:
            raise PreviewError("EXISTING_AGENT_STATE_CHANGED")
        for role, identifier in saved["agents"].items():
            info = api.call("POST", "/agent/search_info", {"agent_id": identifier, "version_no": 0})
            versions = api.call("GET", f"/agent/{identifier}/versions")
            check_owned_draft(info, role, saved)
            if info.get("name") != NAMES[role] or info.get("enabled") is not False or versions.get("total", 0) or info.get("model_ids") != [saved["model_id"]]:
                raise PreviewError("PREVIEW_DRAFT_STATE_CHANGED")
        run_id = preview.begin_run(args.scenario, args.repair_note)
        report["run_id"] = run_id
        body = {"query": PROTOCOL + "\nStart with this exact action, then use the real returned ID:\n" + first_action(args.scenario),
            "agent_id": saved["agents"]["controller"], "model_id": saved["model_id"], "version_no": 0,
            "is_debug": True, "history": [], "requested_output_tokens": 1024,
            "enable_plan": False, "enable_automation_tool": False}
        report["native_run_requests"] = 1
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=body,
            headers={"Authorization": "Bearer " + api.token, "x-user-timezone": "Asia/Shanghai"}) as response:
            response.raise_for_status()
            size = 0
            for line in response.iter_lines():
                size += len(line.encode())
                if size > 2*1024**2 or time.perf_counter() - start > 120:
                    raise PreviewError("NATIVE_RUN_BOUND_EXCEEDED")
                if line.startswith("data:") and line[5:].strip():
                    event = object_from_json(line[5:].strip())
                    if isinstance(event, dict):
                        events.append(event)
        report.update(assess(events, preview, run_id))
        if (report.get("answer") or {}).get("scenario_id", args.scenario) != args.scenario:
            report.update(passed=False, stop_reasons=["REQUESTED_SCENARIO_MISMATCH"])
        if original_signatures(api) != saved["original_signatures"]:
            report.update(passed=False, stop_reasons=["EXISTING_AGENTS_CHANGED"])
    except Exception as error:
        failure = error
        report.update(passed=False, error_type=type(error).__name__)
        if isinstance(error, PreviewError):
            report["error_code"] = str(error)
    finally:
        api.close()
        external = external_failure(events, failure)
        if external:
            with preview.db() as db:
                db.execute("INSERT OR REPLACE INTO flags VALUES('external_block',?)", (external,))
            report['error_code'] = external
        if run_id:
            with preview.db() as db:
                row = db.execute('SELECT payload FROM tasks WHERE run_id=?', (run_id,)).fetchone()
            if row:
                task = json.loads(row[0])
                if task.get('error_code'):
                    report['error_code'] = task['error_code']
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(RUNTIME / (stamp + "-events.json"), events)
        if run_id:
            preview.finish_run(run_id, bool(report.get("passed")))
    report.update(duration_ms=round((time.perf_counter()-start)*1000, 2), campaign=preview.counts(),
        event_types=dict(Counter(e.get("type") for e in events)), cost_amount=None)
    if report.get("answer"):
        task = report["answer"]["task_id"]
        record = read(RUNTIME / (task + "-jev.json"))
        report.update(jev_receipts=record["jev_receipts"], translation_receipts=record["translation_receipts"],
            full_english_input_saved=True, exact_final_matches_persistence=True)
    paths = ["src/gov_mcp/regional_preview.py", "src/gov_mcp/regional_preview_server.py", "src/gov_mcp/knowledge_landing.py", "scripts/verify_regional_native_preview.py",
             "scripts/provision_regional_native_preview.py", "config/skills/govkb-regional-preview/SKILL.md"]
    report["source_sha256"] = {p: source_digest((ROOT / p).read_bytes()) for p in paths}
    out = ROOT / "evidence/regional-native-preview" / (stamp + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    write(out, report)
    print(json.dumps({k: report.get(k) for k in ("passed", "stop_reasons", "error_code", "error_type", "duration_ms", "campaign")}, ensure_ascii=True))
    print(json.dumps({"evidence": str(out)}))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

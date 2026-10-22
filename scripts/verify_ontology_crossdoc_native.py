"""One separately authorized native run, followed by mandatory ontology rollback."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.json_output import exact_json_equal, object_from_json
from gov_mcp.ontology_crossdoc_preview import SKILL, TOOLS, code_fingerprints
from gov_mcp.ontology_crossdoc_preview_server import open_preview
from nexent_api import NexentAPI
from prepare_ontology_crossdoc_native import RUNTIME, OUTPUT, DEMONSTRATION
from provision_crossdoc_native_preview import read, write, fingerprint
from verify_crossdoc_native_preview import trace_summary, external_failure
from check_successor_autonomy_cloud import native_event_failure

STAGES = ("read_skill_md", *["govkb_ontology_crossdoc_" + stage for stage in
          ("open_query", "retrieve", "verify", "state", "finalize")])
MAX_OBSERVED_MODEL_STARTS = 14


def source_pins():
    paths = ("scripts/prepare_ontology_crossdoc_native.py", "scripts/provision_ontology_crossdoc_native.py",
             "scripts/verify_ontology_crossdoc_native.py", "scripts/check_successor_autonomy_cloud.py",
             "scripts/verify_crossdoc_native_preview.py", "scripts/provision_crossdoc_native_preview.py",
             "scripts/nexent_api.py", "config/skills/" + SKILL + "/SKILL.md")
    return {**code_fingerprints(), **{p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}}


def protected_pins():
    saved = read(DEMONSTRATION)["original_pins"]
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in saved}


def child_invocations(events, saved):
    """Nexent reports child starts/ends, not necessarily a wrapper tool event."""
    for role, tool in (("retriever", "retrieve"), ("verifier", "verify")):
        starts = [(i, e) for i, e in enumerate(events) if e.get("type") == "subagent_start"
                  and e.get("agent_id") == saved["agents"][role] and e.get("depth") == 1]
        if len(starts) != 1:
            return False
        begin, start = starts[0]
        invocation = start.get("invocation_id")
        if not isinstance(invocation, str) or not invocation:
            return False
        ends = [i for i, e in enumerate(events) if e.get("type") == "subagent_end"
                and e.get("agent_id") == saved["agents"][role] and e.get("depth") == 1
                and e.get("invocation_id") == invocation]
        calls = [(i, e) for i, e in enumerate(events) if e.get("type") == "tool"
                 and e.get("tool_name") == "govkb_ontology_crossdoc_" + tool]
        if (len(ends) != 1 or len(calls) != 1 or not begin < calls[0][0] < ends[0]
                or calls[0][1].get("agent_id") != saved["agents"][role]
                or calls[0][1].get("invocation_id") != invocation):
            return False
    return True


def assess(events, preview, run_id, saved):
    calls = [(i, e) for i, e in enumerate(events) if e.get("type") == "tool"]
    names = [e.get("tool_name") for _, e in calls]
    child_names = {saved["draft_bodies"][r]["name"] for r in ("retriever", "verifier")}
    stages = [e for _, e in calls if e.get("tool_name") in STAGES]
    checks = {"exact_ordered_stages": [e.get("tool_name") for e in stages] == list(STAGES),
              "no_extra_tools": set(names) <= set(STAGES) | child_names | {"final_answer"},
              "one_call_per_stage": all(names.count(name) == 1 for name in STAGES),
              "two_children": [(e.get("agent_id"), e.get("depth")) for e in events
                               if e.get("type") == "subagent_start"] ==
                              [(saved["agents"][r], 1) for r in ("retriever", "verifier")],
              "child_invocation_bindings": child_invocations(events, saved)
                  and all(names.count(name) <= 1 for name in child_names),
              "stage_depths": all(e.get("depth", 0) == (1 if e.get("tool_name") in {
                  "govkb_ontology_crossdoc_retrieve", "govkb_ontology_crossdoc_verify"} else 0) for e in stages),
              "no_native_errors": not any(native_event_failure(e) for e in events)}
    starts = [e for e in events if e.get("type") == "model_attempt_control" and e.get("phase") == "begin"]
    checks["observed_model_budget"] = bool(starts) and len(starts) <= MAX_OBSERVED_MODEL_STARTS and all(
        type(e.get("attempt")) is int and 1 <= e["attempt"] <= 3 for e in starts)
    answer = None
    try:
        finals = [(i, e.get("content")) for i, e in enumerate(events)
                  if e.get("type") == "final_answer" and e.get("depth", 0) == 0]
        if len(finals) != 1:
            raise ValueError("ONE_ROOT_FINAL_REQUIRED")
        answer = object_from_json(finals[0][1]) if isinstance(finals[0][1], str) else finals[0][1]
        task = preview.get_task(answer["task_id"])
        proof, context = answer["reasoning"], answer["ontology_context"]
        checks.update(
            persisted_answer=task["run_id"] == run_id and task["status"] == "complete"
                and exact_json_equal(task["answer"], answer)
                and exact_json_equal(preview.finalize(answer["task_id"]), answer),
            reviewed_plan_executed=proof["reason"] == "REVIEWED_ONTOLOGY_PLAN_EXECUTED"
                and context["version"] == preview.expected_version
                and context == proof["ontology_context"],
            typed_dependent_trace=[n["depends_on"] for n in proof["trace"]] == [[], ["c1"], ["c1", "u1"]]
                and [n["output"] for n in proof["trace"]] == [25, 22, "false"]
                and all(n["ontology_context_binding"] == context["binding"] and n["evidence"] for n in proof["trace"]),
            paused_business=answer["published"] is False and answer["formal_approval"] is False
                and proof["business_conclusion_status"] == "PAUSED_UNREVIEWED"
                and proof["current_applicability"] == "unknown",
            final_after_tool=finals[0][0] > max(i for i, e in calls
                                               if e.get("tool_name") == "govkb_ontology_crossdoc_finalize"))
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        checks["structured_persisted_answer"] = False
    return {"passed": all(checks.values()), "checks": checks,
            "answer": answer if all(checks.values()) else None,
            "actual_tools": names, "observed_model_starts": len(starts)}


def plan():
    return {"status": "PREPARED_NOT_EXECUTED", "native_run_budget": 1,
            "controller_max_steps": 6, "child_max_steps_each": 3,
            "protocol_format_repairs_per_step_max": 2,
            "observed_model_start_stop_threshold": MAX_OBSERVED_MODEL_STARTS,
            "max_output_tokens_per_step": 1024, "whole_run_retries": 0,
            "cost_amount": None, "usage_note": "Native steps and observed starts are not a provider invoice or a hard monetary cap.",
            "source_scope": "Two archived public Nanjing documents and fixed fictional facts only.",
            "required": "Separate current human authorization for one paid native run; --live --confirmed --reviewer NAME.",
            "model_calls": 0, "http_calls": 0, "formally_published": False}


def live(api, saved, reviewer):
    from provision_ontology_crossdoc_native import (check_drafts, check_skill, check_service,
                                                   other_signatures, effective_other_signatures,
                                                   PROTOCOL, first_action)
    preview = open_preview(RUNTIME)
    binding = read(RUNTIME / "ontology-binding.json")
    actor = {"kind": "human", "name": reviewer}
    before, implementation = protected_pins(), source_pins()
    others = other_signatures(api, saved["agents"].values())
    if others != effective_other_signatures(saved):
        raise ValueError("OTHER_AGENT_STATE_CHANGED")
    check_service()
    check_drafts(api, saved)
    check_skill(api, saved)
    if preview.counts()["native_runs"] != 0:
        raise ValueError("NATIVE_CAMPAIGN_BUDGET_EXHAUSTED")
    if preview.workspace.status()["head"] != binding["baseline"]:
        raise ValueError("EXPECTED_BASELINE_REQUIRED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {**plan(), "status": "RUNNING", "recorded_at": stamp,
              "human_authorization": {"actor": actor, "scope": "one_native_run_activate_then_rollback"},
              "source_sha256": implementation, "passed": False, "native_run_requests": 0,
              "model_calls": None, "http_calls": None}
    events, run_id, conversation, activated = [], None, None, False
    started = time.monotonic()
    try:
        preview.workspace.rollback(binding["expected_version"], binding["baseline"], actor, confirmed=True)
        activated = True
        context = preview.preflight()
        run_id = preview.begin_run()
        report.update(run_id=run_id, ontology_context=context["ontology_context"])
        write(RUNTIME / (stamp + "-authorization.json"), report)
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "Reviewed ontology cross-document preview"})["data"]["conversation_id"]
        request = {"query": PROTOCOL + "\nStart with:\n" + first_action(),
                   "conversation_id": conversation, "agent_id": saved["agents"]["controller"],
                   "model_id": saved["model_id"], "version_no": 0, "is_debug": True,
                   "history": [], "requested_output_tokens": 1024,
                   "enable_plan": False, "enable_automation_tool": False}
        write(RUNTIME / (stamp + "-request.json"), request)
        report.update(native_run_requests=1, request_sha256=fingerprint(request))
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=request,
                headers={"Authorization": "Bearer " + api.token}, timeout=httpx.Timeout(120, connect=10)) as response:
            response.raise_for_status()
            size = 0
            for line in response.iter_lines():
                with (RUNTIME / (stamp + "-sse.log")).open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
                size += len(line.encode())
                if size > 2 * 1024**2 or time.monotonic() - started > 120:
                    raise ValueError("NATIVE_RUN_BOUND_EXCEEDED")
                if line.startswith("data:") and line[5:].strip():
                    event = object_from_json(line[5:].strip())
                    events.append(event)
                    with (RUNTIME / (stamp + "-events.jsonl")).open("a", encoding="utf-8") as journal:
                        journal.write(json.dumps(event, ensure_ascii=False) + "\n")
                    failure = native_event_failure(event)
                    if failure:
                        raise ValueError(failure)
                    if sum(e.get("type") == "model_attempt_control" and e.get("phase") == "begin"
                           for e in events) > MAX_OBSERVED_MODEL_STARTS:
                        raise ValueError("OBSERVED_MODEL_START_STOP_THRESHOLD_EXCEEDED")
        report.update(assess(events, preview, run_id, saved))
        check_drafts(api, saved)
        check_skill(api, saved)
        if other_signatures(api, saved["agents"].values()) != others or source_pins() != implementation or protected_pins() != before:
            raise ValueError("PROTECTED_ASSET_OR_IMPLEMENTATION_CHANGED")
    except Exception as error:
        code = external_failure(events, error)
        if not code:
            code = str(error) if re.fullmatch(r"[A-Z_]+", str(error)) else "NATIVE_EXECUTION_UNAVAILABLE"
        report.update(passed=False, error_code=code, error_type=type(error).__name__)
    finally:
        if not report["passed"] and conversation is not None:
            try:
                api.runtime_call("GET", "/agent/stop/" + str(conversation))
            except Exception:
                report["native_stop_unconfirmed"] = True
        try:
            if run_id:
                preview.end_run(run_id, bool(report["passed"]), report.get("error_code"))
        except Exception:
            report.update(passed=False, campaign_close_failed=True)
        finally:
            if activated:
                try:
                    preview.workspace.rollback(binding["baseline"], binding["expected_version"], actor, confirmed=True)
                except Exception:
                    report.update(passed=False, rollback_failed=True)
        write(RUNTIME / (stamp + "-events.json"), events)
        report.update(status="PASSED" if report["passed"] else "FAILED",
                      campaign=preview.counts(), ontology_head=preview.workspace.status()["head"],
                      rolled_back=preview.workspace.status()["head"] == binding["baseline"],
                      duration_ms=round((time.monotonic() - started) * 1000, 2),
                      event_types=dict(Counter(e.get("type") for e in events)), trace=trace_summary(events),
                      private_events_sha256=hashlib.sha256((RUNTIME / (stamp + "-events.json")).read_bytes()).hexdigest())
        write(OUTPUT / (stamp + ".json"), report)
        write(OUTPUT / "latest.json", {"evidence": stamp + ".json", "passed": report["passed"], "run_id": run_id})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    if not args.live:
        result = plan()
        write(OUTPUT / "native-run-plan.json", result)
    else:
        if not args.confirmed or not args.reviewer or not 1 <= len(args.reviewer.strip()) <= 100:
            parser.error("EXPLICIT_CURRENT_NATIVE_RUN_CONFIRMATION_REQUIRED")
        api = NexentAPI()
        try:
            api.login(read(ROOT / ".runtime/accounts.json")["developer"])
            result = live(api, read(RUNTIME / "native-drafts.json"), args.reviewer)
        finally:
            api.close()
    print(json.dumps({k: result.get(k) for k in ("status", "passed", "error_code", "rolled_back", "campaign")}, ensure_ascii=True))
    return 0 if not args.live or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""One authorized Henan three-source Nexent campaign, with isolated rollback.

Reuses the existing authenticated MCP registration and configured DeepSeek
model. Existing Agents, source approvals and spent native ledgers are protected.
The scan is an attributed existing AI transcription, not a new OCR/API read.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from check_native_ready import probe_services
from inspect_agent_workflow import inspect as inspect_workflow
from check_successor_autonomy_cloud import native_event_failure
from gov_mcp.deepseek import PROFILES
from gov_mcp.henan_multimodal import FACTS, PROFILE
from gov_mcp.henan_multimodal_preview import HENAN_SCENARIO, code_fingerprints
from gov_mcp.json_output import exact_json_equal, object_from_json
from gov_mcp.knowledge.store import digest
from gov_mcp.ontology_crossdoc_preview_server import open_preview
from nexent_api import NexentAPI
from private_runtime import ensure_private_directory
from provision_henan_multimodal_native import profile_provisioner
from run_henan_multimodal import preserved_inputs
from verify_crossdoc_native_preview import external_failure, trace_summary
from verify_ontology_crossdoc_native import STAGES, child_invocations

RUNTIME = ROOT / ".runtime/henan-multimodal-native-20261022"
OUTPUT = ROOT / "evidence/henan-multimodal-native-20261022"
OLD_NATIVE = ROOT / ".runtime/ontology-crossdoc-native-20261021"
OLD_ONTOLOGY = ROOT / ".runtime/henan-multimodal-20261022/ontology"
MAX_MODEL_STARTS = 14


def read(path):
    return object_from_json(path.read_bytes())


def write(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    values = preserved_inputs()
    paths = [p for p in OLD_ONTOLOGY.rglob("*") if p.is_file()]
    paths.append(OLD_ONTOLOGY.parent / "gates.json")
    for directory in (OLD_NATIVE, ROOT / ".runtime/system-integration-20261022/native"):
        paths += [directory / name for name in
                  ("crossdoc.sqlite3", "access.json", "native-drafts.json", "ontology-binding.json")]
    paths += [ROOT / "evidence/henan-multimodal-20261022" / name for name in ("candidate.json", "summary.json")]
    values.update({path.relative_to(ROOT).as_posix(): sha(path) for path in paths})
    return values


def source_pins():
    paths = ("scripts/run_henan_multimodal_native.py", "scripts/provision_henan_multimodal_native.py",
             "scripts/provision_ontology_crossdoc_native.py", "scripts/nexent_api.py",
             "scripts/verify_ontology_crossdoc_native.py", "scripts/verify_crossdoc_native_preview.py",
             "scripts/check_successor_autonomy_cloud.py", "scripts/provision_crossdoc_native_preview.py",
             "config/skills/govkb-henan-multimodal-preview/SKILL.md")
    return {**code_fingerprints(), **{name: sha(ROOT / name) for name in paths}}


def prepare():
    previous = ROOT / "evidence/henan-multimodal-20261022"
    artifact = read(previous / "candidate.json")
    demonstrations = [read(p) for p in previous.glob("demonstration-*.json")]
    matches = [d for d in demonstrations if d.get("candidate_id") == artifact["candidate_id"]
               and d.get("baseline") == artifact["expected_head"]
               and d.get("status") == "PASS_LOCAL_HENAN_ARCHIVE_DEMO"
               and all(d["checks"].values()) and d["current_head"] == d["baseline"]]
    if len(matches) != 1 or preserved_inputs() != artifact["protected_inputs"]:
        raise ValueError("ONE_COMPLETED_CURRENT_LOCAL_HENAN_DEMO_REQUIRED")
    demo = matches[0]
    target = RUNTIME / "ontology"
    if not target.exists():
        ensure_private_directory(target)
        shutil.copyfile(OLD_ONTOLOGY / "ontology-workspace.json", target / "ontology-workspace.json")
        shutil.copytree(OLD_ONTOLOGY / "objects", target / "objects")
        with sqlite3.connect((OLD_ONTOLOGY / "knowledge.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(target / "knowledge.sqlite3") as destination:
                source.backup(destination)
    binding = {"profile": PROFILE, "ontology_root": str(target),
               "baseline": demo["baseline"], "expected_version": demo["activated_version"],
               "candidate_id": demo["candidate_id"],
               "henan_source": {
                   "preparation_root": str(ROOT / ".runtime/multimodal-preparation-20261021"),
                   "observation_path": str(ROOT / "evidence/competition-multimodal-20261021/scan-visual-observation.json"),
                   "extraction_path": str(ROOT / "evidence/competition-multimodal-20261021/local-extraction.json"),
                   "gates_path": str(ROOT / ".runtime/henan-multimodal-20261022/gates.json")}}
    path = RUNTIME / "ontology-binding.json"
    if path.exists():
        if not exact_json_equal(read(path), binding):
            raise ValueError("EXISTING_HENAN_NATIVE_BINDING_CHANGED")
    else:
        write(path, binding, exclusive=True)
    access = RUNTIME / "access.json"
    if access.exists():
        if access.read_bytes() != (OLD_NATIVE / "access.json").read_bytes():
            raise ValueError("EXISTING_MCP_ACCESS_BINDING_CHANGED")
    else:
        shutil.copyfile(OLD_NATIVE / "access.json", access)
    preview = open_preview(RUNTIME)
    workspace = preview.workspace
    if workspace.status()["head"] != binding["baseline"]:
        raise ValueError("EXPECTED_ROLLED_BACK_BASELINE_REQUIRED")
    approved = workspace._version(binding["expected_version"])
    workspace._guard_version(approved, workspace._current())
    record = workspace.store.get("candidates", binding["candidate_id"])
    if (record["status"] != "accepted_structure_only" or record["version"] != binding["expected_version"]
            or record["review"]["actor"]["kind"] != "human"):
        raise ValueError("EXACT_ACCEPTED_HENAN_STRUCTURE_REQUIRED")
    if preview.counts()["native_runs"] != 0 or (RUNTIME / "model-attempt.json").exists():
        raise ValueError("HENAN_NATIVE_CAMPAIGN_BUDGET_EXHAUSTED")
    return preview, binding


def assess(events, preview, run_id, saved):
    calls = [(i, e) for i, e in enumerate(events) if e.get("type") == "tool"]
    names = [e.get("tool_name") for _, e in calls]
    children = {saved["draft_bodies"][role]["name"] for role in ("retriever", "verifier")}
    stages = [e for _, e in calls if e.get("tool_name") in STAGES]
    starts = [e for e in events if e.get("type") == "model_attempt_control" and e.get("phase") == "begin"]
    checks = {
        "exact_ordered_stages": [e.get("tool_name") for e in stages] == list(STAGES),
        "no_extra_tools": set(names) <= set(STAGES) | children | {"final_answer"},
        "one_call_per_stage": all(names.count(name) == 1 for name in STAGES),
        "two_children": [(e.get("agent_id"), e.get("depth")) for e in events if e.get("type") == "subagent_start"]
            == [(saved["agents"][role], 1) for role in ("retriever", "verifier")],
        "child_invocation_bindings": child_invocations(events, saved) and all(names.count(name) <= 1 for name in children),
        "stage_depths": all(e.get("depth", 0) == (1 if e.get("tool_name") in {
            "govkb_ontology_crossdoc_retrieve", "govkb_ontology_crossdoc_verify"} else 0) for e in stages),
        "no_native_errors": not any(native_event_failure(e) for e in events),
        "observed_model_budget": bool(starts) and len(starts) <= MAX_MODEL_STARTS and all(
            type(e.get("attempt")) is int and 1 <= e["attempt"] <= 3 for e in starts)}
    answer = None
    try:
        finals = [(i, e.get("content")) for i, e in enumerate(events)
                  if e.get("type") == "final_answer" and e.get("depth", 0) == 0]
        if len(finals) != 1:
            raise ValueError("ONE_ROOT_FINAL_REQUIRED")
        answer = object_from_json(finals[0][1]) if isinstance(finals[0][1], str) else finals[0][1]
        task = preview.get_task(answer["task_id"])
        proof, context = answer["reasoning"], answer["ontology_context"]
        current = preview.workspace.query(FACTS, expected_version=preview.expected_version)
        documents = preview.workspace._current()["candidate"]["source_documents"]
        evidence = [e for node in proof["trace"] for e in node["evidence"]]
        required = {(doc["source_id"], s["locator"], s["text_sha256"]) for doc in documents.values() for s in doc["segments"]}
        checks.update(
            persisted_answer=task["run_id"] == run_id and task["status"] == "complete"
                and exact_json_equal(task["answer"], answer) and exact_json_equal(preview.finalize(answer["task_id"]), answer),
            reviewed_henan_plan_executed=proof["status"] == "derived_archive" and context["profile"] == PROFILE
                and context["version"] == preview.expected_version and exact_json_equal(proof, current)
                and exact_json_equal(context, proof["ontology_context"]),
            four_typed_dependent_nodes=[node["id"] for node in proof["trace"]]
                == ["catalog_basis", "fee_branch", "service_archive", "archive_answer"]
                and [node["depends_on"] for node in proof["trace"]]
                == [[], ["catalog_basis"], [], ["fee_branch", "service_archive"]]
                and all(node["ontology_context_binding"] == context["binding"] for node in proof["trace"]),
            all_seventeen_quotes_bound=required == {(e["source_id"], e["locator"], e["text_sha256"]) for e in evidence}
                and all(hashlib.sha256(e["quote"].encode()).hexdigest() == e["text_sha256"] for e in evidence)
                and len(required) == 17,
            scope_and_scan_limits=answer.get("profile") == PROFILE and answer.get("scenario_id") == HENAN_SCENARIO
                and documents["scan"]["visual_provenance"]["human_review_completed"] is False
                and proof["archive_result"]["historical_duration"]["unit"] == "day_unspecified",
            paused_business=answer["published"] is False and answer["formal_approval"] is False
                and proof["business_conclusion_status"] == "PAUSED_UNREVIEWED"
                and proof["current_applicability"] == "unknown" and proof["actual_fee_yuan"] is None
                and proof["promised_duration"] is None and proof["formally_published"] is False,
            final_after_tool=finals[0][0] > max(i for i, e in calls if e.get("tool_name") == "govkb_ontology_crossdoc_finalize"))
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        checks["structured_persisted_answer"] = False
    return {"passed": all(checks.values()), "checks": checks,
            "answer": answer if all(checks.values()) else None,
            "actual_tools": names, "observed_model_starts": len(starts)}


def plan():
    return {"status": "PLANNED_NOT_EXECUTED", "mode": "henan_three_source_reviewed_native_preview", "scenario_id": HENAN_SCENARIO,
            "native_runs_max": 1, "whole_run_retries": 0, "observed_model_starts_max": MAX_MODEL_STARTS,
            "controller_steps": 6, "child_steps_each": 3, "max_output_tokens_per_step": 1024,
            "new_disabled_drafts": 3, "port": 8785, "cost_amount": None,
            "usage_note": "Observed starts and token events do not provide a hard monetary cap or a provider invoice.",
            "model_scope": "Configured official DeepSeek language model; no new image recognition",
            "business_publication": False, "requires": "--live --confirmed --reviewer NAME"}


def live(reviewer):
    ensure_private_directory(RUNTIME)
    before, pins = protected(), source_pins()
    initial = inspect_workflow()
    report = {**plan(), "status": "RUNNING", "passed": False,
              "started_at": datetime.now(timezone.utc).isoformat(), "source_sha256": pins,
              "human_authorization": {"actor": {"kind": "human", "name": reviewer},
                    "scope": "Current user started the reviewed Henan demo and authorized API calls; one isolated native run then rollback"},
              "protected_before_sha256": before, "native_run_requests": 0, "old_budgets_reset": False}
    helper = api = preview = binding = run_id = conversation = None
    activated, events = False, []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    started = time.monotonic()
    actor = {"kind": "human", "name": reviewer}
    try:
        if not initial["safe_to_recover"] or not initial["heartbeat_fresh"]:
            raise ValueError("PAUSED_HEALTHY_WORKFLOW_REQUIRED")
        report["platform_ready"] = probe_services()
        if not all(report["platform_ready"].values()):
            raise ValueError("NATIVE_PLATFORM_NOT_READY")
        preview, binding = prepare()
        provisioning = profile_provisioner(RUNTIME, OUTPUT)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8785)) == 0:
                raise ValueError("EXISTING_HELPER_PRESERVED_PORT_OCCUPIED")
        with (RUNTIME / "service.stdout.log").open("ab") as stdout, (RUNTIME / "service.stderr.log").open("ab") as stderr:
            helper = subprocess.Popen([sys.executable, "-B", "-m", "gov_mcp.ontology_crossdoc_preview_server",
                "--runtime", str(RUNTIME), "--port", "8785"], cwd=ROOT, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(40):
            if helper.poll() is not None:
                raise ValueError("HENAN_HELPER_START_FAILED")
            try:
                provisioning.check_service()
                break
            except httpx.HTTPError:
                time.sleep(.25)
        else:
            raise ValueError("HENAN_HELPER_START_TIMEOUT")
        token = read(RUNTIME / "access.json")["mcp_token"]
        asyncio.run(provisioning.handshake(token))
        api = NexentAPI()
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        draft_path = RUNTIME / "native-drafts.json"
        saved = read(draft_path) if draft_path.exists() else {"agents": {}, "published": False}
        report["provisioning"] = provisioning.provision(api, saved, token)
        saved = read(draft_path)
        models = api.call("GET", "/model/list")["data"]
        model = next(m for m in models if m["model_id"] == saved["model_id"])
        if (model.get("model_name") != PROFILES["official"]["model"]
                or model.get("base_url", "").rstrip("/") != PROFILES["official"]["base_url"]):
            raise ValueError("EXPECTED_OFFICIAL_MODEL_BINDING_REQUIRED")
        others = provisioning.other_signatures(api, saved["agents"].values())
        if others != provisioning.effective_other_signatures(saved):
            raise ValueError("OTHER_AGENT_STATE_CHANGED")
        write(RUNTIME / "model-attempt.json", {"at": stamp, "authorization": report["human_authorization"],
              "native_runs_max": 1, "whole_run_retries": 0}, exclusive=True)
        preview.workspace.rollback(binding["expected_version"], binding["baseline"], actor, confirmed=True)
        activated = True
        context = preview.preflight()
        run_id = preview.begin_run()
        report.update(run_id=run_id, ontology_context=context["ontology_context"])
        write(RUNTIME / (stamp + "-authorization.json"), report, exclusive=True)
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "河南三模态归档预览"})["data"]["conversation_id"]
        request = {"query": provisioning.PROTOCOL + "\nStart with:\n" + provisioning.first_action(),
                   "conversation_id": conversation, "agent_id": saved["agents"]["controller"],
                   "model_id": saved["model_id"], "version_no": 0, "is_debug": True,
                   "history": [], "requested_output_tokens": 1024, "enable_plan": False, "enable_automation_tool": False}
        write(RUNTIME / (stamp + "-request.json"), request, exclusive=True)
        report.update(native_run_requests=1, request_sha256=digest(request))
        native_started = time.monotonic()
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=request,
                headers={"Authorization": "Bearer " + api.token}, timeout=httpx.Timeout(120, connect=10)) as response:
            response.raise_for_status()
            size = 0
            with (RUNTIME / (stamp + "-sse.log")).open("x", encoding="utf-8") as stream:
                for line in response.iter_lines():
                    stream.write(line + "\n")
                    stream.flush()
                    size += len(line.encode())
                    if size > 2 * 1024**2 or time.monotonic() - native_started > 120:
                        raise ValueError("NATIVE_RUN_BOUND_EXCEEDED")
                    if not line.startswith("data:") or not line[5:].strip():
                        continue
                    event = object_from_json(line[5:].strip())
                    events.append(event)
                    failure = native_event_failure(event)
                    if failure:
                        raise ValueError(failure)
                    if sum(e.get("type") == "model_attempt_control" and e.get("phase") == "begin" for e in events) > MAX_MODEL_STARTS:
                        raise ValueError("OBSERVED_MODEL_START_STOP_THRESHOLD_EXCEEDED")
        report["native_duration_ms"] = round((time.monotonic() - native_started) * 1000, 2)
        report.update(assess(events, preview, run_id, saved))
        provisioning.check_drafts(api, saved)
        provisioning.check_skill(api, saved)
        report["other_agents_unchanged"] = provisioning.other_signatures(api, saved["agents"].values()) == others
        if not report["other_agents_unchanged"] or source_pins() != pins or protected() != before:
            raise ValueError("PROTECTED_ASSET_OR_IMPLEMENTATION_CHANGED")
    except Exception as error:
        code = external_failure(events, error)
        if not code:
            code = str(error) if re.fullmatch(r"[A-Z_]+", str(error)) else "HENAN_NATIVE_EXECUTION_UNAVAILABLE"
        report.update(passed=False, error_code=code, error_type=type(error).__name__)
    finally:
        if not report["passed"] and conversation is not None:
            try:
                api.runtime_call("GET", "/agent/stop/" + str(conversation))
            except Exception:
                report["native_stop_unconfirmed"] = True
        if preview is not None:
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
            try:
                report.update(campaign=preview.counts(), rolled_back=preview.workspace.status()["head"] == binding["baseline"])
            except Exception:
                report.update(passed=False, cleanup_state_unavailable=True)
        if api is not None:
            try:
                api.close()
            except Exception:
                report.update(passed=False, api_close_failed=True)
        if helper is not None:
            try:
                helper.terminate()
                try:
                    helper.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait(timeout=10)
                report["owned_helper_stopped"] = helper.poll() is not None
            except Exception:
                report.update(passed=False, owned_helper_stopped=False)
        try:
            after = inspect_workflow()
            report["workflow_head_versions_and_pause_unchanged"] = all(after[key] == initial[key] for key in ("head", "version_count", "paused"))
            report["workflow_heartbeat_fresh"] = after["heartbeat_fresh"]
            report["workflow_safe_after"] = after["safe_to_recover"]
        except Exception:
            report.update(passed=False, workflow_state_unavailable=True, workflow_safe_after=False)
        try:
            report["protected_inputs_unchanged"] = protected() == before
        except Exception:
            report.update(passed=False, protected_inputs_unchanged=False)
        report["passed"] = bool(report["passed"] and report["protected_inputs_unchanged"]
            and report.get("workflow_head_versions_and_pause_unchanged") and report["workflow_safe_after"]
            and report.get("owned_helper_stopped") and report.get("rolled_back"))
        write(RUNTIME / (stamp + "-events.json"), events, exclusive=True)
        report.update(status="PASSED" if report["passed"] else "FAILED", ended_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=round((time.monotonic() - started) * 1000, 2), trace=trace_summary(events),
            private_events_sha256=sha(RUNTIME / (stamp + "-events.json")), event_types=dict(Counter(e.get("type") for e in events)))
        write(OUTPUT / (stamp + ".json"), report, exclusive=True)
        write(OUTPUT / "latest.json", {"evidence": stamp + ".json", "passed": report["passed"], "run_id": run_id})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    if args.live:
        if not args.confirmed or not args.reviewer or not 1 <= len(args.reviewer.strip()) <= 100:
            parser.error("CURRENT_USER_NATIVE_AUTHORIZATION_REQUIRED")
        result = live(args.reviewer)
    else:
        result = plan()
    public = {key: result.get(key) for key in ("status", "passed", "error_code", "campaign", "rolled_back",
        "observed_model_starts", "native_run_requests", "native_duration_ms", "protected_inputs_unchanged")} if args.live else result
    print(json.dumps(public, ensure_ascii=False))
    return 0 if not args.live or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

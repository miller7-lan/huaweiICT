"""One explicit integrated exercise; old ledgers and ontology remain untouched.

Runs the existing synthetic engineering lifecycle and one real Nanjing native
chain separately. It does not claim the pending Henan multimodal chain ran.
No registration, publication, account setup, or global inference activation.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from check_native_ready import probe_services
from check_project_heartbeat import inspect as inspect_system
from inspect_agent_workflow import inspect as inspect_workflow
from private_runtime import ensure_private_directory
import prepare_ontology_crossdoc_native as preparation
import provision_ontology_crossdoc_native as provisioning
import verify_ontology_crossdoc_native as native
from verify_agent_workflow import run as engineering_run
from gov_mcp.ontology_crossdoc_preview_server import open_preview
from gov_mcp.deepseek import PROFILES
from nexent_api import NexentAPI

OUT = ROOT / "evidence/system-integration-20261022"
PRIVATE = ROOT / ".runtime/system-integration-20261022"
NEW_NATIVE = PRIVATE / "native"
NEW_ONTOLOGY = PRIVATE / "ontology"
OLD_NATIVE = ROOT / ".runtime/ontology-crossdoc-native-20261021"
OLD_ONTOLOGY = ROOT / ".runtime/ontology-crossdoc-20261020/ontology"


def read(path):
    return json.loads(path.read_text("utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    paths = [OLD_NATIVE / name for name in ("crossdoc.sqlite3", "access.json", "native-drafts.json", "ontology-binding.json")]
    paths += [p for p in OLD_ONTOLOGY.rglob("*") if p.is_file()]
    paths += [ROOT / "config/decision/connections.json"]
    paths += [p for folder in ("evidence/json-stability-20261021", "evidence/json-stability-repair-20261021")
              for p in (ROOT / folder).iterdir() if p.is_file()]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}


def clone_reviewed_ontology():
    if NEW_ONTOLOGY.exists():
        raise ValueError("NEW_ONTOLOGY_DIRECTORY_MUST_NOT_EXIST")
    ensure_private_directory(NEW_ONTOLOGY)
    shutil.copyfile(OLD_ONTOLOGY / "ontology-workspace.json", NEW_ONTOLOGY / "ontology-workspace.json")
    shutil.copytree(OLD_ONTOLOGY / "objects", NEW_ONTOLOGY / "objects")
    with sqlite3.connect((OLD_ONTOLOGY / "knowledge.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(NEW_ONTOLOGY / "knowledge.sqlite3") as target:
            source.backup(target)
    # No native ledger is copied. Only the same reviewed immutable structure.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    args = parser.parse_args()
    if not (args.live and args.confirmed):
        print(json.dumps({"mode": "plan_only", "native_runs_max": 1, "observed_model_starts_max": 14,
                          "controller_steps": 6, "child_steps_each": 3, "whole_run_retries": 0,
                          "model_calls": 0, "http_calls": 0, "requires": "--live --confirmed"}))
        return 0
    ensure_private_directory(PRIVATE)
    # Reserve this overall attempt before any model request. Never reset it.
    with (PRIVATE / "attempt.json").open("x", encoding="utf-8") as handle:
        json.dump({"at": datetime.now(timezone.utc).isoformat(), "native_runs_max": 1,
                   "authorization": "Current user requested one complete invocation, continued it, and requested push.",
                   "scope": "same reviewed Nanjing archive structure; isolated activation then rollback"}, handle)
    before = protected()
    initial = inspect_workflow()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "RUNNING",
              "before": initial, "protected_before_sha256": before, "passed": False,
              "new_native_runs_max": 1, "old_budgets_reset": False,
              "scope": "Read-only system checks, isolated synthetic lifecycle, and one real native Nanjing chain.",
              "not_covered": ["Pending new Henan multimodal structure activation", "Independent business evaluation",
                              "Clean-machine import", "Long-running real-model compliance"],
              "global_inference_enabled": False, "business_published": False}
    helper = None
    api = None
    try:
        if not initial["safe_to_recover"] or not initial["heartbeat_fresh"]:
            raise ValueError("PAUSED_HEALTHY_WORKFLOW_REQUIRED")
        report["platform_ready"] = probe_services()
        if not all(report["platform_ready"].values()):
            raise ValueError("NATIVE_PLATFORM_NOT_READY")
        report["system_before"] = inspect_system()
        engineering_run(OUT / "engineering")
        report["synthetic_engineering"] = read(OUT / "engineering/verification.json")
        clone_reviewed_ontology()
        ensure_private_directory(NEW_NATIVE)
        # Existing MCP registration and owned drafts retain their exact token.
        # This is private-to-private copying, not credential output or rotation.
        shutil.copyfile(OLD_NATIVE / "access.json", NEW_NATIVE / "access.json")
        shutil.copyfile(OLD_NATIVE / "native-drafts.json", NEW_NATIVE / "native-drafts.json")
        preparation.RUNTIME, preparation.OUTPUT, preparation.ONTOLOGY = NEW_NATIVE, OUT / "native", NEW_ONTOLOGY
        provisioning.RUNTIME, provisioning.OUTPUT = NEW_NATIVE, OUT / "native"
        native.RUNTIME, native.OUTPUT = NEW_NATIVE, OUT / "native"
        report["preparation"] = preparation.prepare()
        if report["preparation"]["campaign"]["native_runs"] != 0:
            raise ValueError("FRESH_UNSPENT_CAMPAIGN_REQUIRED")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8785)) == 0:
                raise ValueError("EXISTING_HELPER_PRESERVED_PORT_OCCUPIED")
        stdout = (PRIVATE / "service.stdout.log").open("wb")
        stderr = (PRIVATE / "service.stderr.log").open("wb")
        try:
            helper = subprocess.Popen([sys.executable, "-B", "-m", "gov_mcp.ontology_crossdoc_preview_server",
                                       "--runtime", str(NEW_NATIVE), "--port", "8785"], cwd=ROOT,
                                      stdout=stdout, stderr=stderr,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            stdout.close()
            stderr.close()
        for _ in range(40):
            if helper.poll() is not None:
                raise ValueError("HELPER_START_FAILED")
            try:
                provisioning.check_service()
                break
            except Exception:
                time.sleep(.25)
        else:
            raise ValueError("HELPER_START_TIMEOUT")
        asyncio.run(provisioning.handshake(read(NEW_NATIVE / "access.json")["mcp_token"]))
        api = NexentAPI()
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        saved = read(NEW_NATIVE / "native-drafts.json")
        provisioning.check_drafts(api, saved)
        provisioning.check_skill(api, saved)
        models = api.call("GET", "/model/list")["data"]
        model = next(row for row in models if row["model_id"] == saved["model_id"])
        if (model.get("model_name") != PROFILES["official"]["model"]
                or model.get("base_url", "").rstrip("/") != PROFILES["official"]["base_url"]):
            raise ValueError("EXPECTED_OFFICIAL_MODEL_BINDING_REQUIRED")
        report["official_model_binding_checked"] = True
        report["native"] = native.live(api, saved, "user-confirmed-single-system-invocation")
        report["passed"] = bool(report["native"]["passed"] and report["native"]["rolled_back"]
                                and report["synthetic_engineering"]["passed"] == report["synthetic_engineering"]["total"])
    except Exception as error:
        report.update(passed=False, error_type=type(error).__name__)
        # Unexpected exception text can contain private paths or upstream data.
        if isinstance(error, ValueError) and str(error).replace("_", "").isupper():
            report["error_code"] = str(error)
    finally:
        if api:
            api.close()
        if helper:
            helper.terminate()
            try:
                helper.wait(timeout=10)
            except subprocess.TimeoutExpired:
                helper.kill()
                helper.wait(timeout=10)
            report["owned_helper_stopped"] = helper.poll() is not None
        if (NEW_NATIVE / "ontology-binding.json").exists():
            try:
                preview = open_preview(NEW_NATIVE)
                binding = read(NEW_NATIVE / "ontology-binding.json")
                current = preview.workspace.status()["head"]
                if current != binding["baseline"]:
                    preview.workspace.rollback(binding["baseline"], current,
                        {"kind": "human", "name": "user-confirmed-single-system-invocation"}, confirmed=True)
                report["final_campaign"] = preview.counts()
                report["isolated_ontology_rolled_back"] = preview.workspace.status()["head"] == binding["baseline"]
            except Exception as error:
                report.update(passed=False, cleanup_error_type=type(error).__name__)
        report["after"] = inspect_workflow()
        report["protected_inputs_unchanged"] = protected() == before
        report["workflow_head_and_versions_unchanged"] = all(report["after"][key] == initial[key] for key in ("head", "version_count", "paused"))
        report["passed"] = bool(report["passed"] and report["protected_inputs_unchanged"]
                                and report["workflow_head_and_versions_unchanged"] and report["after"]["safe_to_recover"]
                                and report.get("owned_helper_stopped") and report.get("isolated_ontology_rolled_back"))
        report["status"] = "PASSED" if report["passed"] else "FAILED"
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["executed_wrapper_sha256"] = sha(Path(__file__))
        write(OUT / "summary.json", report)
        print(json.dumps({key: report.get(key) for key in ("status", "passed", "error_code", "error_type", "final_campaign",
                         "protected_inputs_unchanged", "workflow_head_and_versions_unchanged", "owned_helper_stopped")}, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Actual sidecar native storage lock, connection outage and post-fault recovery."""

import json
import hashlib
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx

from gov_mcp.core import BlankStore
from gov_mcp.extension import ExtensionStore
from nexent_api import ROOT, RUNTIME
from verify_m1_native import run


@contextmanager
def extension_lock(docker):
    marker = "/tmp/gov-m1-lock-" + uuid.uuid4().hex
    code = """import pathlib,select,sqlite3,sys
db=sqlite3.connect('/runtime/extension.sqlite3',timeout=2)
try:
    db.execute('BEGIN EXCLUSIVE')
    pathlib.Path(sys.argv[1]).write_text('LOCKED')
    select.select([sys.stdin],[],[],300)
finally:
    db.rollback()
    db.close()
    pathlib.Path(sys.argv[1]).unlink(missing_ok=True)
"""
    process = subprocess.Popen([docker, "exec", "-i", "govx-blank-mcp", "python", "-c", code, marker],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(40):
            if process.poll() is not None:
                raise RuntimeError("Extension lock ended before acquisition")
            probe = subprocess.run([docker, "exec", "govx-blank-mcp", "test", "-f", marker], capture_output=True, timeout=5)
            if probe.returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Extension lock acquisition not confirmed")
        yield marker
    finally:
        try:
            process.communicate(input=b"\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise RuntimeError("Extension lock release not confirmed")
        if process.returncode != 0:
            raise RuntimeError("Extension lock process failed")


def require_service(url):
    with httpx.Client(trust_env=False, timeout=3) as client:
        for _ in range(30):
            try:
                if client.get(url).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise RuntimeError("Service recovery readiness not confirmed")


def capture_ui(expected_code):
    proc = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli", "playwright-cli",
        "-s=workbench", "run-code", "--filename", "scripts/m1_fault_ui.js"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode or "### Error" in proc.stdout:
        raise RuntimeError("Actual browser fault operation failed")
    value = json.loads(proc.stdout.split("### Result", 1)[1].split("###", 1)[0].strip())
    assert value["result"]["status"] == "ERROR" and value["result"]["message_code"] == expected_code
    assert value["result"]["data"]["role"] == "citizen" and value["role_text"] == "已核定业务角色：群众"
    entries = [json.loads(line) for line in (RUNTIME / "m1/workbench-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    value["workbench_audit"] = next(a for a in reversed(entries) if a["result"] == value["result"])
    if expected_code == "TOOL_TIMEOUT":
        audit = [json.loads(line) for line in (RUNTIME / "m1/mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()]
        value["mcp_audit"] = next(a for a in audit if a["request_id"] == value["result"]["data"]["request_id"])
        assert value["mcp_audit"]["result"] == value["result"]
        assert value["workbench_audit"]["principal_sha256"] == value["mcp_audit"]["principal_sha256"]
    else:
        assert "request_id" not in value["result"]["data"]
        value["mcp_audit"] = None
    value["screenshot_sha256"] = hashlib.sha256((ROOT / value["screenshot"]).read_bytes()).hexdigest()
    return value


def main():
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI required")
    sidecar = json.loads((ROOT / "evidence/m1-native-sidecar-success.json").read_text(encoding="utf-8"))
    if sidecar.get("passed") is not True:
        raise ValueError("Sidecar success path must pass before fault injection")
    state = json.loads((RUNTIME / "m1/nexent-state.json").read_text(encoding="utf-8"))
    records = {}
    destination = ROOT / "evidence/m1-native-faults.json"
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "runtime_url": "http://127.0.0.1:5016", "records": records}
    def checkpoint():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint()
    try:
        require_service("http://127.0.0.1:8766/health")
        with extension_lock(docker) as marker:
            print("Govx container lock acquired; running native timeout case.", flush=True)
            report["storage_timeout_ui"] = capture_ui("TOOL_TIMEOUT")
            records["storage_timeout"] = run("fresh_citizen", "storage-timeout", state,
                terminal=("ERROR", "TOOL_TIMEOUT"), runtime_url=report["runtime_url"])
            records["storage_timeout"]["fault"] = {"kind": "actual exclusive SQLite lock inside MCP container",
                "database": ".runtime/m1/mcp/extension.sqlite3", "lock_acquisition_confirmed": True,
                "marker": marker, "release_confirmed": False}
        records["storage_timeout"]["fault"]["release_confirmed"] = True
        checkpoint()
        records["storage_recovery"] = run("fresh_citizen", "storage-recovery", state, runtime_url=report["runtime_url"])
        checkpoint()
        print("Stopping only govx MCP; original MCP and original runtime remain running.", flush=True)
        subprocess.run([docker, "stop", "govx-blank-mcp"], capture_output=True, check=True, timeout=30)
        try:
            stopped = json.loads(subprocess.check_output([docker, "inspect", "govx-blank-mcp"], timeout=10))[0]
            if stopped["State"]["Running"]:
                raise RuntimeError("Govx outage was not confirmed")
            report["connection_timeout_ui"] = capture_ui("CONNECTION_OR_TOOL_UNAVAILABLE")
            records["connection_timeout"] = run("fresh_citizen", "connection-timeout", state,
                terminal=("ERROR", "CONNECTION_TIMEOUT"), runtime_url=report["runtime_url"], connection_failure=True)
            records["connection_timeout"]["fault"] = {"kind": "actual govx container stopped", "outage_confirmed": True,
                "restore_confirmed": False}
            checkpoint()
        finally:
            subprocess.run([docker, "start", "govx-blank-mcp"], capture_output=True, check=True, timeout=30)
            require_service("http://127.0.0.1:8766/health")
        records["connection_timeout"]["fault"]["restore_confirmed"] = True
        records["connection_recovery"] = run("fresh_citizen", "connection-recovery", state, runtime_url=report["runtime_url"])
        report["original_counts"] = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
        report["extension_counts"] = ExtensionStore(RUNTIME / "m1/mcp/extension.sqlite3").counts()
        report["passed"] = (all(r["passed"] for r in records.values()) and
            not any(report["original_counts"].values()) and not any(report["extension_counts"].values()))
        checkpoint()
        print("M1 native faults", "PASS" if report["passed"] else "FAIL", flush=True)
    except Exception as error:
        report["failure_type"] = type(error).__name__
        checkpoint()
        raise
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

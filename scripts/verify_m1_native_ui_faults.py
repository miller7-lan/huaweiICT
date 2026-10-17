"""Real native M1 page terminal responses via sidecar routing, with fault cleanup."""

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

from nexent_api import NexentAPI, ROOT, RUNTIME
from verify_m1_native_faults import extension_lock, require_service
from verify_native_faults import root_final


def capture(code, principal):
    audit_path = RUNTIME / "m1/mcp/tool-audit.jsonl"
    offset = audit_path.stat().st_size
    proc = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli", "playwright-cli",
        "-s=m1", "run-code", "--filename", "scripts/m1_native_terminal_ui.js"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    if proc.returncode or "### Error" in proc.stdout:
        (RUNTIME / "m1/browser/native-fault-error.txt").write_text(proc.stdout, encoding="utf-8")
        raise RuntimeError("Native UI terminal operation failed; details retained privately")
    value = json.loads(proc.stdout.split("### Result",1)[1].split("### Ran Playwright code",1)[0].strip())
    with audit_path.open("rb") as source:
        source.seek(offset)
        audit = [json.loads(line) for line in source if line.strip()]
    audit = [a for a in audit if a["principal_sha256"] == principal]
    result = value["result"]
    clean = not any(e["type"] in ("subagent_start","subagent_end","max_steps_reached","error") for e in value["events"])
    passed = clean and result["status"] == "ERROR" and result["message_code"] == code and result["items"] == result["citations"] == []
    if code == "TOOL_TIMEOUT":
        passed = passed and len(audit)==1 and audit[0]["tool"]=="govx_get_environment_status" and result==audit[0]["result"] and result["data"]["attempts"]==2
    else:
        passed = passed and not audit and result["data"]["attempts"]==1 and result["data"]["phase"]=="mcp_connection" and not any(
            e["type"]=="tool" or e["type"].startswith("model_output") for e in value["events"])
    value.update(passed=bool(passed),audit=audit,principal_sha256=principal,
        screenshot_sha256=hashlib.sha256((ROOT/value["screenshot"]).read_bytes()).hexdigest())
    assert type(value["conversation_id"]) is int and value["conversation_id"]>0
    assert root_final(value["events"]) == result
    return value


def main():
    docker = shutil.which("docker")
    api = NexentAPI()
    try:
        user=api.login(json.loads((RUNTIME/"m1/accounts.json").read_text(encoding="utf-8"))["fresh_citizen"])
    finally:api.close()
    principal=hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
    output={"captured_at":datetime.now(timezone.utc).isoformat(),"passed":False,"source":"actual M1 native UI entry routed to isolated runtime","records":{}}
    dest=ROOT/"evidence/m1-native-ui-faults.json"
    def save():dest.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    save()
    with extension_lock(docker):
        output["records"]["storage_timeout"]=capture("TOOL_TIMEOUT",principal)
    output["records"]["storage_timeout"]["lock_released"]=True
    save()
    subprocess.run([docker,"stop","govx-blank-mcp"],capture_output=True,check=True,timeout=30)
    try:
        output["records"]["connection_timeout"]=capture("CONNECTION_TIMEOUT",principal)
    finally:
        subprocess.run([docker,"start","govx-blank-mcp"],capture_output=True,check=True,timeout=30)
        require_service("http://127.0.0.1:8766/health")
    output["records"]["connection_timeout"]["service_restored"]=True
    output["passed"]=all(r["passed"] for r in output["records"].values())
    save()
    print("Native M1 UI fault cases", "PASS" if output["passed"] else "FAIL")
    raise SystemExit(0 if output["passed"] else 1)


if __name__=="__main__":main()

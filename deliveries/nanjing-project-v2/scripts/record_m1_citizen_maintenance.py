"""Record actual browser maintenance denial and match each response to service audit."""

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    proc = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli", "playwright-cli",
        "-s=workbench", "run-code", "--filename", "scripts/m1_citizen_maintenance.js"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode or "### Error" in proc.stdout:
        print(proc.stdout)
        raise RuntimeError("Actual browser maintenance operations failed")
    value = json.loads(proc.stdout.split("### Result", 1)[1].split("###", 1)[0].strip())
    assert value["passed"]
    api = NexentAPI()
    try:
        user = api.login(json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))["fresh_citizen"])
    finally:
        api.close()
    principal = hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
    entries = [json.loads(line) for line in (RUNTIME / "m1/mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    index = {a["request_id"]: a for a in entries}
    for entry in value["trace"]:
        entry["audit"] = index[entry["result"]["data"]["request_id"]]
        assert entry["audit"]["principal_sha256"] == principal and entry["audit"]["result"] == entry["result"]
        entry["screenshot_sha256"] = hashlib.sha256((ROOT / entry["screenshot"]).read_bytes()).hexdigest()
    value.update(captured_at=datetime.now(timezone.utc).isoformat(), source="agent-operated real workbench UI", principal_sha256=principal)
    (ROOT / "evidence/m1-citizen-maintenance-ui.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BT11 browser: citizen prepare/publish denied; screenshots and actual response/audit IDs matched.")


if __name__ == "__main__":
    main()

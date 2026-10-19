import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    api = NexentAPI()
    try:
        user = api.login(json.loads((RUNTIME / "m1/accounts.json").read_text())["fresh_citizen"])
    finally:
        api.close()
    principal = hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
    def count():
        lines = (RUNTIME / "m1/mcp/tool-audit.jsonl").read_text(encoding="utf-8").splitlines()
        return sum(json.loads(line).get("principal_sha256") == principal for line in lines)
    before = count()
    proc = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli", "playwright-cli",
        "-s=m1", "run-code", "--filename", "scripts/m1_empty_input.js"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    output = proc.stdout
    if proc.returncode or "### Error" in output:
        raise RuntimeError("Browser empty-input operation failed")
    value = json.loads(output.split("### Result", 1)[1].split("###", 1)[0].strip())
    after = count()
    assert value["assertion"] and before == after
    value.update(captured_at=datetime.now(timezone.utc).isoformat(), tool_calls_before=before,
        tool_calls_after=after, screenshot="output/playwright/m1-BT02.png",
        scope="Native UI empty and whitespace Enter; no agent/run dispatch means no model execution was requested")
    (ROOT / "evidence/m1-empty-input.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BT02 native empty/whitespace Enter: send disabled, zero Agent/model dispatches, tool audit count unchanged.")


if __name__ == "__main__":
    main()

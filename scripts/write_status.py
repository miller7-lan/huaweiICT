"""Record completed local checks separately from blocked native acceptance."""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.core import BlankStore


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / "evidence"
    def read(file, default=None):
        return json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else default
    suite = ET.parse(evidence / "unit-tests.xml").getroot().find("testsuite")
    transport = read(evidence / "sse-transport.json", {})
    native_state = read(root / ".runtime/nexent-state.json", {})
    status = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "core_chain_accepted": False, "selected_model": None,
        "platform": {"version": "v2.7.0", "commit": "519b9b17c7f4cbc623256ac318ea96a20f33772e",
                     "docker_desktop_installed": True, "docker_engine": "UNAVAILABLE_WSL_NOT_ENABLED",
                     "upstream_changes": "loopback-only port bindings; no service or SQL changes"},
        "automated_tests": {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")},
        "real_sse": {"passed": transport.get("passed", False), "actual_calls": len(transport.get("records", [])),
                     "scope": "isolated SSE tool layer, including one injected technical storage timeout"},
        "business_counts": BlankStore(root / ".runtime/mcp/business.sqlite3").counts(),
        "downloaded_models": read(evidence / "model-downloads.json", {}),
        "model_parameter_checks": read(evidence / "model-settings.json", {}),
        "native_accounts_created": len(native_state.get("accounts", {})),
        "native_agents_created": len(native_state.get("agents", {})),
        "native_core_cases_executed": 0, "native_ui_verified": False,
        "blocked_work": ["Nexent containers", "real native account provisioning", "native UI checks",
                         "40 native core runs", "additional native fault tests", "model selection"],
        "blocker": {"code": "ADMIN_REQUIRED_FOR_WSL2", "evidence": "Windows optional features disabled; WSL installer returned 0x80073d28",
                    "next_action": "In Administrator PowerShell run wsl --install --no-distribution; follow Windows restart instructions, then rerun scripts/deploy.ps1"},
        "excluded": ["official business asset import", "mock policies/cases/application materials", "BT01-BT16 acceptance", "business accuracy or algorithm validation"],
    }
    (evidence / "implementation-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Implementation status saved; native core acceptance remains incomplete.")


if __name__ == "__main__":
    main()

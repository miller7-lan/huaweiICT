"""Bind the real local service restart to unchanged business state and new diagnostics."""

import argparse
import json
from datetime import datetime, timezone

import httpx

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from nexent_api import ROOT, RUNTIME
from verify_access_stop_diagnostics import canonical_binding


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()
    runtime = RUNTIME / "knowledge-update"
    store = KnowledgeStore(runtime, read_only=True)
    with store.db() as db:
        running = db.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
        index = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='source_check_failures'").fetchone() is not None
    binding = canonical_binding()
    processes = read(runtime / "service-state.json")
    baseline = runtime / "access-stop-deployment-baseline.json"
    if args.before:
        if running or (runtime / "service-paused.json").exists():
            raise ValueError("Do not restart during a live job or an existing manual pause")
        baseline.write_text(json.dumps({"binding": binding, "processes": processes}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"active_jobs": running, "before_recorded": True, "counts": binding["counts"]}))
        return
    before = read(baseline)
    with httpx.Client(trust_env=False, timeout=10) as http:
        response = http.get("http://127.0.0.1:8772/api/status"); response.raise_for_status()
        status = response.json()
        page = http.get("http://127.0.0.1:8772/"); page.raise_for_status()
        mcp = http.get("http://127.0.0.1:8773/health"); mcp.raise_for_status()
    checks = {"candidate_state_source_configs_and_policy_unchanged": before["binding"] == binding,
              "supervisor_and_all_components_restarted": processes["supervisor_pid"] != before["processes"]["supervisor_pid"]
              and all(pid != before["processes"]["processes"][key] for key, pid in processes["processes"].items()),
              "deployed_status_has_diagnostic_field_and_preserves_unknown_legacy": all("access_stop" in source for source in status["sites"])
              and all(source["access_stop"] is None for source in status["sites"] if source["paused_reason"]),
              "deployed_workbench_has_phase_specific_and_unknown_labels": "robots访问检查未通过" in page.text
              and "具体检查阶段未记录" in page.text,
              "failure_lookup_index_prepared": index,
              "worker_and_maintenance_heartbeats_fresh": all(any(w["id"] == key and not w["stale"] for w in status["workers"])
                                                         for key in ("knowledge-worker", "knowledge-maintenance")),
              "mcp_stays_read_only": mcp.json().get("status") == "UP" and mcp.json().get("read_only") is True,
              "existing_windows_recovery_healthy": status["startup"]["health"]["code"] == "healthy",
              "no_running_job_or_manual_pause": running == 0 and not (runtime / "service-paused.json").exists()}
    screenshot = ROOT / "evidence/knowledge-update/access-stop-fixture.png"
    if screenshot.exists() and screenshot.read_bytes().startswith(b"\xff\xd8"):
        target = screenshot.with_suffix(".jpg")
        if target.exists():
            raise ValueError("Screenshot target already exists")
        screenshot.rename(target)
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
              "business_binding": binding, "source_count": len(status["sites"]),
              "legacy_paused_sources_without_phase": [s["id"] for s in status["sites"] if s["paused_reason"] and s["access_stop"] is None],
              "modules": {name: fingerprint((ROOT / "src/gov_mcp" / name).read_bytes()) for name in ("knowledge.py", "knowledge_fetch.py", "knowledge_workbench.html")},
              "scope": "Local deployed API/UI, supervisor replacement, fresh workers and read-only MCP. No new official source access, model test, business approval, reboot or full-week acceptance."}
    (ROOT / "evidence/knowledge-update/access-stop-deployment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "source_count": len(status["sites"])}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

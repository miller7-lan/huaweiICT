"""Passive local heartbeat inspection: no ticks, scheduling changes or model calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def inspect():
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "inspection_only": True,
              "model_requests": 0, "tasks_changed": False, "runtimes": {}, "ports": {}}
    for name, keys in (("agent-workflow", ("wf_control", "wf_last_error")), ("knowledge", ("heartbeat", "paused")), ("knowledge-demo", ("heartbeat", "paused"))):
        path = ROOT / ".runtime" / name / "knowledge.sqlite3"
        state = {"exists": path.is_file()}
        if path.is_file():
            with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as db:
                for key in keys:
                    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
                    if row:
                        value = json.loads(row[0])
                        if isinstance(value, dict):
                            value = {k: v for k, v in value.items() if k in {"heartbeat_at", "at", "status", "interval", "paused", "code"}}
                            at = value.get("heartbeat_at", value.get("at"))
                            if isinstance(at, (int, float)):
                                value["age_seconds"] = round(time.time()-at, 2)
                        state[key] = value
                if name == "agent-workflow":
                    state["source_count"] = db.execute("SELECT COUNT(*) FROM wf_records WHERE kind='source'").fetchone()[0]
                    state["heartbeat_errors_last_hour"] = db.execute("SELECT COUNT(*) FROM audit WHERE operation='workflow_heartbeat_error' AND time>?", (time.time()-3600,)).fetchone()[0]
        report["runtimes"][name] = state
    for name in ("knowledge", "knowledge-demo"):
        path = ROOT / ".runtime" / name / "heartbeat.json"
        if path.exists():
            config = json.loads(path.read_text("utf-8-sig"))
            report["runtimes"].setdefault(name, {}).update(config_enabled=config.get("enabled"),
                heartbeat_seconds=config.get("heartbeat_seconds"), check_interval_seconds=config.get("check_interval_seconds"),
                demo_mode=config.get("demo_mode"), configured_source_count=len(config.get("sources", [])))
    for port in (8772, 8773, 8775, 8778, 8780, 8781):
        with socket.socket() as probe:
            probe.settimeout(1)
            report["ports"][str(port)] = probe.connect_ex(("127.0.0.1", port)) == 0
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        code = "@{tasks=@(Get-ScheduledTask -ErrorAction Stop | Where-Object {$_.TaskName -like 'HuaweiICT-KnowledgeUpdate-*'} | ForEach-Object {@{name=$_.TaskName;state=[string]$_.State;enabled=$_.Settings.Enabled}})} | ConvertTo-Json -Depth 5 -Compress"
        value = subprocess.check_output(["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code], timeout=20, creationflags=flags)
        report["windows_recovery"] = json.loads(value)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        report["windows_recovery"] = {"status": "unknown", "error_type": type(error).__name__}
    try:
        template = '{"name":{{json .Name}},"restart_policy":{{json .HostConfig.RestartPolicy.Name}},"state":{{json .State.Status}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}'
        value = subprocess.check_output(["docker", "inspect", "--format", template, "govkb-maintenance", "govkb-demo"], timeout=15, creationflags=flags, stderr=subprocess.PIPE)
        report["docker_recovery"] = [json.loads(line) for line in value.decode().splitlines() if line.strip()]
        flag = subprocess.check_output(["docker", "inspect", "--format", '{{range .Config.Env}}{{if eq . "GOVKB_SYNTHETIC_REPLAY=1"}}synthetic_replay{{end}}{{end}}', "govkb-demo"], timeout=10, creationflags=flags, stderr=subprocess.PIPE)
        report["demo_uses_synthetic_replay"] = flag.decode().strip() == "synthetic_replay"
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        report["docker_recovery"] = {"status": "unknown", "error_type": type(error).__name__}
    path = ROOT / ".runtime/public-native-preview/preview.sqlite3"
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as db:
            report["public_preview"] = {"timer_registered": False,
                "native_runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                "jev_reserved": (db.execute("SELECT value FROM counters WHERE name='jev_reserved'").fetchone() or [0])[0],
                "campaign_passed_and_stopped": bool(db.execute("SELECT 1 FROM flags WHERE name='passed'").fetchone())}
    return report


if __name__ == "__main__":
    value = inspect()
    output = ROOT / "evidence/public-native-preview/heartbeat-check.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", "utf-8")
    print(json.dumps(value, ensure_ascii=True, indent=2))

"""Record real maintenance state and whether an unchanged later tick stayed quiet."""

import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore
from nexent_api import ROOT, RUNTIME


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    status = store.status()
    state = status["maintenance"]
    with store.db() as db:
        changed_events = db.execute("SELECT COUNT(*) FROM audit WHERE action='maintenance_state_changed'").fetchone()[0]
    sample = {"last_checked": state["last_checked"], "signal": state["signal"], "change_event_count": changed_events}
    baseline_path = store.root / "maintenance-observation-baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    else:
        baseline = sample
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    elapsed = sample["last_checked"]-baseline["last_checked"]
    quiet = (sample["change_event_count"] == baseline["change_event_count"]) if elapsed >= 60 and sample["signal"] == baseline["signal"] else None
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "backup_day": state.get("backup_day"),
        "restore_week": state.get("restore_week"), "last_backup": state.get("last_backup"),
        "last_restore": state.get("last_restore"), "current_alerts": state.get("alerts"),
        "workers": [{"id": row["id"], "stale": row["stale"], "progress": row["progress"]} for row in status["workers"]],
        "baseline_sample": baseline, "latest_sample": sample, "elapsed_between_maintenance_ticks_seconds": elapsed,
        "unchanged_state_kept_quiet": quiet, "natural_full_week_observed": False,
        "scope": "First real backup/recovery run and bounded unchanged-state observation; not a long-term unattended or production-deployment acceptance"}
    (ROOT / "evidence/knowledge-update/maintenance-first-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backup_day": report["backup_day"], "restore_week": report["restore_week"],
                      "observed_tick_gap_seconds": elapsed, "unchanged_state_kept_quiet": quiet}, ensure_ascii=False))


if __name__ == "__main__":
    main()

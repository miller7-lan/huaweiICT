"""Quiet local maintenance: daily bundles, weekly restore checks and actionable status changes."""

import argparse
import json
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_backup import create_bundle, restore_bundle
from gov_mcp.knowledge_lock import process_lock
from gov_mcp.knowledge_startup import observe_startup_task


def maintenance_lock(root):
    return process_lock(root / "maintenance.lock")


def maintenance_tick(store, now=None, owner="maintenance-once", backup=create_bundle, restore=restore_bundle):
    now = time.time() if now is None else now
    path = store.root / "maintenance-status.json"
    with maintenance_lock(store.root) as acquired:
        if not acquired:
            return {"skipped": "another maintenance process holds the lock"}
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(state, dict):
                raise ValueError("Maintenance state must be an object")
        except (OSError, ValueError) as error:
            state = {"errors": {"state": "Previous maintenance state unreadable: "+str(error)[:200]}}
        previous_signal = state.get("signal")
        try:
            observe_startup_task(store)
            state.setdefault("errors", {}).pop("startup_observation", None)
        except OSError:
            state.setdefault("errors", {})["startup_observation"] = "Windows recovery task observation could not be saved"
        local = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
        day, week = local.strftime("%Y-%m-%d"), "{}-W{:02d}".format(*local.isocalendar()[:2])
        errors = state.setdefault("errors", {})
        with store.db() as db:
            db.execute("INSERT INTO workers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,progress=excluded.progress", (owner, now, "maintenance"))
        for action, due in (("backup", state.get("backup_day") != day), ("restore", state.get("restore_week") != week)):
            if not due or action in errors and now-state.get(action+"_last_attempt", 0) < 3600:
                continue
            state[action+"_last_attempt"] = now
            try:
                if action == "backup":
                    if shutil.disk_usage(store.root).free < 1024**3:
                        raise OSError("Available disk space is below 1 GiB; maintenance backup deferred")
                    target = store.root / "backups" / ("daily-"+day+"-"+uuid.uuid4().hex[:8])
                    state["last_backup"] = backup(store, target)
                    state["backup_day"] = day
                else:
                    if not state.get("last_backup"):
                        raise ValueError("No completed data bundle available for weekly recovery check")
                    target = store.root / "recovery-checks" / ("weekly-"+week+"-"+uuid.uuid4().hex[:8])
                    state["last_restore"] = restore(state["last_backup"]["directory"], target)
                    state["restore_week"] = week
                errors.pop(action, None)
            except (OSError, ValueError, sqlite3.Error) as error:
                errors[action] = str(error)[:300]
        status = store.status()
        alerts = []
        startup = status["startup"]["health"]
        if startup["level"] == "warning":
            alerts.append({"code": "startup_recovery_attention", "reason": startup["code"]})
        for source in status["sites"]:
            if source["enabled"] and (source["paused_reason"] or source["stale"]):
                alerts.append({"code": "source_paused" if source["paused_reason"] else "source_stale", "source": source["id"]})
            elif source["enabled"] and source["failures"] > 0:
                alerts.append({"code": "source_check_failed", "source": source["id"]})
        failed = [job["id"] for job in status["jobs"] if job["state"] == "failed"]
        if failed:
            alerts.append({"code": "failed_jobs", "job_ids": failed})
        for discovery in status["discovery"]:
            if discovery["limited"] or discovery["parser_truncated"]:
                alerts.append({"code": "discovery_limited", "source": discovery["source_id"], "unvisited_cached_targets": discovery["unvisited_cached_targets"]})
        if status["pending_review"]:
            alerts.append({"code": "pending_review", "revision_ids": sorted(row["id"] for row in status["pending_review"])})
        stale = [worker["id"] for worker in status["workers"] if worker["stale"] and worker["id"] != owner]
        if stale:
            alerts.append({"code": "worker_stale", "workers": stale})
        alerts.extend({"code": action+"_failed", "reason": reason} for action, reason in sorted(errors.items()))
        state.update(last_checked=now, alerts=alerts, schedule_timezone="Asia/Shanghai", automatically_delete_backups=False)
        state["signal"] = fingerprint({"alerts": alerts, "backup": state.get("last_backup", {}).get("manifest_sha256"),
                                       "restore": state.get("last_restore", {}).get("source_manifest_sha256")})
        changed = previous_signal != state["signal"]
        with store.db() as db:
            if changed:
                store.log(db, "maintenance_state_changed", {"alerts": alerts, "backup_day": state.get("backup_day"), "restore_week": state.get("restore_week")})
            db.execute("UPDATE workers SET heartbeat=?,progress='idle' WHERE id=?", (now, owner))
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return {"changed": changed, "backup_day": state.get("backup_day"), "restore_week": state.get("restore_week"), "alerts": alerts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = KnowledgeStore(args.runtime)
    owner = "maintenance-once-"+uuid.uuid4().hex[:8] if args.once else "knowledge-maintenance"
    while True:
        result = maintenance_tick(store, owner=owner)
        if result.get("changed"):
            print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once:
            with store.db() as db:
                db.execute("UPDATE workers SET progress='completed' WHERE id=?", (owner,))
            break
        time.sleep(60)


if __name__ == "__main__":
    main()

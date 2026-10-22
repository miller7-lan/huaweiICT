"""Observe the existing Windows recovery task without modifying its settings."""

import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def startup_health(startup, now=None):
    now = time.time() if now is None else now
    registration, recovery, task = (startup.get(key) or {} for key in ("registration", "recovery", "task"))
    def outcome(code, message, level="warning"):
        return {"code": code, "message": message, "level": level}
    if startup.get("manual_pause"):
        return outcome("paused", "已手动暂停本机自动恢复。", "info")
    if registration.get("status") == "unreadable":
        return outcome("registration_unreadable", "自动恢复登记不可读，请核查本机配置。")
    if not registration.get("installed"):
        return outcome("not_registered", "尚未登记当前用户自动恢复。", "info")
    if not task:
        return outcome("task_not_observed", "尚无Windows恢复任务实时检查，旧登记不能证明任务仍在运行。")
    checked = task.get("checked_at")
    if type(checked) not in (float, int) or not math.isfinite(checked) or checked > now+60 or now-checked > 180:
        return outcome("task_observation_stale", "Windows恢复任务的实时检查已过期。")
    if task.get("status") == "missing":
        return outcome("task_missing", "Windows恢复任务已不存在，自动恢复不可用。")
    if task.get("status") != "observed":
        return outcome("task_audit_failed", "无法核对Windows恢复任务，自动恢复状态未知。")
    if task.get("ownership_verified") is not True:
        return outcome("task_ownership_mismatch", "Windows恢复任务归属不符，需要核查。")
    if task.get("enabled") is not True:
        return outcome("task_disabled", "Windows恢复任务已停用，自动恢复当前不可用。")
    if task.get("configuration_matches") is not True:
        return outcome("task_configuration_changed", "Windows恢复任务的登录或每5分钟触发配置已变化。")
    in_progress = (task.get("last_result"), task.get("task_state")) in ((0x41301, "Running"), (0x41325, "Queued"))
    if task.get("last_result") != 0 and not in_progress:
        return outcome("task_last_run_failed", "Windows恢复任务最近执行未成功，不能沿用此前的健康记录。")
    last_check = recovery.get("checked_at")
    if type(last_check) not in (float, int) or not math.isfinite(last_check) or last_check > now+60 or now-last_check > 420:
        return outcome("recovery_check_overdue", "自动恢复检查记录已超时，不能确认恢复任务正常执行。")
    if recovery.get("ready") is not True or recovery.get("action") not in ("healthy", "recovered"):
        return outcome("recovery_not_ready", "最近自动恢复检查未确认服务就绪。")
    return outcome("healthy", "Windows恢复任务已启用，最近恢复检查正常。", "info")


def observe_startup_task(store):
    path = store.root / "startup-registration.json"
    if not path.exists():
        return None
    checked = time.time()
    try:
        registration = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(registration, dict):
            raise ValueError("Invalid registration")
        if not registration.get("installed"):
            return None
        if sys.platform != "win32":
            value = {"status": "unsupported_platform"}
        else:
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            completed = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                "-File", str(Path(__file__).with_name("knowledge_startup_windows.ps1")), "-RuntimePath", str(store.root)],
                capture_output=True, encoding="utf-8-sig", timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value = json.loads(completed.stdout)
            if not isinstance(value, dict) or value.get("status") not in ("observed", "missing", "unavailable"):
                raise ValueError("Invalid task observation")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        value = {"status": "unavailable", "error_kind": type(error).__name__}
    value.update(schema=1, checked_at=checked, read_only_observation=True)
    target = store.root / "startup-task-status.json"
    temporary = target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return value

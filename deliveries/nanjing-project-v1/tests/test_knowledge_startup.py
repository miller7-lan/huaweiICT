"""OS task observations must override old registration/success records."""

import json
import subprocess
import time

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_maintenance import maintenance_tick
from gov_mcp.knowledge_startup import observe_startup_task, startup_health


def healthy(now):
    return {"registration": {"installed": True, "task_state": "Ready"},
            "recovery": {"checked_at": now, "ready": True, "action": "healthy"},
            "task": {"checked_at": now, "status": "observed", "task_state": "Ready", "last_result": 0, "enabled": True,
                     "ownership_verified": True, "configuration_matches": True}}


def write_state(store, state):
    for key, name in (("registration", "startup-registration.json"), ("recovery", "startup-recovery-status.json"), ("task", "startup-task-status.json")):
        (store.root / name).write_text(json.dumps(state[key]), encoding="utf-8")


def test_disabled_task_overrides_old_success_without_reader_mutation(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-startup")
    state = healthy(time.time()); state["task"]["enabled"] = False; state["task"]["task_state"] = "Disabled"
    write_state(store, state)
    before = store.path.read_bytes()
    value = KnowledgeStore(store.root, read_only=True).status()["startup"]
    assert value["health"]["code"] == "task_disabled" and value["health"]["level"] == "warning"
    assert store.path.read_bytes() == before and value["recovery"]["ready"] is True
    (store.root / "startup-task-status.json").write_text('[]', encoding="utf-8")
    assert store.status()["startup"]["health"]["level"] == "warning"


def test_missing_failed_stale_misowned_and_changed_tasks_never_report_healthy():
    now = time.time()
    scenarios = [({"status": "missing"}, "task_missing"), ({"status": "unavailable"}, "task_audit_failed"),
        ({"ownership_verified": False}, "task_ownership_mismatch"), ({"configuration_matches": False}, "task_configuration_changed"),
        ({"last_result": 5}, "task_last_run_failed"),
        ({"last_result": 5, "task_state": "Running"}, "task_last_run_failed"),
        ({"checked_at": now-181}, "task_observation_stale"), ({"checked_at": now+61}, "task_observation_stale"),
        ({"checked_at": float('nan')}, "task_observation_stale")]
    for change, expected in scenarios:
        state = healthy(now); state["task"].update(change)
        assert startup_health(state, now)["code"] == expected
    state = healthy(now); state["task"] = None
    assert startup_health(state, now)["code"] == "task_not_observed"
    state = healthy(now); state["recovery"]["checked_at"] = now-421
    assert startup_health(state, now)["code"] == "recovery_check_overdue"


def test_only_recent_verified_task_is_healthy_and_manual_pause_is_respected():
    now = time.time(); state = healthy(now)
    assert startup_health(state, now)["code"] == "healthy"
    for result, task_state in ((0x41301, "Running"), (0x41325, "Queued")):
        state['task'].update(last_result=result, task_state=task_state)
        assert startup_health(state, now)['code'] == 'healthy'
    state["manual_pause"] = True; state["task"]["enabled"] = False
    assert startup_health(state, now) == {"code": "paused", "message": "已手动暂停本机自动恢复。", "level": "info"}


def test_maintenance_notifies_changes_but_not_each_identical_task_observation(tmp_path, monkeypatch):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-startup")
    clock = [time.time()]; state = healthy(clock[0]); state["task"]["enabled"] = False
    monkeypatch.setattr('gov_mcp.knowledge_startup.time.time', lambda: clock[0])
    def observe(_):
        state["task"]["checked_at"] = clock[0]; state["recovery"]["checked_at"] = clock[0]
        write_state(store, state)
    monkeypatch.setattr('gov_mcp.knowledge_maintenance.observe_startup_task', observe)
    first = maintenance_tick(store)
    assert any(alert == {"code": "startup_recovery_attention", "reason": "task_disabled"} for alert in first["alerts"])
    clock[0] += 60
    assert maintenance_tick(store)["changed"] is False
    state["task"]["enabled"] = True; clock[0] += 60
    restored = maintenance_tick(store)
    assert restored["changed"] and not any(alert["code"] == "startup_recovery_attention" for alert in restored["alerts"])


def test_os_query_timeout_is_bounded_hidden_and_does_not_leak_stderr(tmp_path, monkeypatch):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-startup")
    write_state(store, healthy(time.time()))
    monkeypatch.setattr('gov_mcp.knowledge_startup.sys.platform', 'win32')
    monkeypatch.setenv('SystemRoot', str(tmp_path))
    seen = []
    def timeout(command, **options):
        seen.append((command, options))
        raise subprocess.TimeoutExpired(command, options['timeout'], stderr='sensitive failure text')
    monkeypatch.setattr('gov_mcp.knowledge_startup.subprocess.run', timeout)
    value = observe_startup_task(store)
    assert value['status'] == 'unavailable' and value['error_kind'] == 'TimeoutExpired'
    assert 'sensitive' not in json.dumps(value) and value['read_only_observation']
    command, options = seen[0]
    assert options['timeout'] == 15 and '-WindowStyle' in command and 'Hidden' in command
    assert not any('Enable-ScheduledTask' in part or 'Register-ScheduledTask' in part for part in command)
    assert store.status()['startup']['health']['code'] == 'task_audit_failed'


def test_unregistered_runtime_does_not_query_or_modify_os(tmp_path, monkeypatch):
    store = KnowledgeStore.initialize(tmp_path / "synthetic-startup")
    def forbidden(*args, **kwargs):
        raise AssertionError('No OS query expected')
    monkeypatch.setattr('gov_mcp.knowledge_startup.subprocess.run', forbidden)
    assert observe_startup_task(store) is None and not (store.root / 'startup-task-status.json').exists()

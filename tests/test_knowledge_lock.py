"""Real process-lock contention and crash release; no network or policy fixtures."""

import json
import os
import signal
import subprocess
import sys

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_lock import process_lock


def test_lock_contends_across_processes_and_releases_after_owner_is_killed(tmp_path):
    path = tmp_path / "supervisor.lock"
    code = """import json,os,sys
from pathlib import Path
from gov_mcp.knowledge_lock import process_lock
with process_lock(Path(sys.argv[1])) as acquired:
    print(json.dumps({'pid':os.getpid(),'acquired':acquired}),flush=True)
    if acquired:
        sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    owner = None
    try:
        owner = json.loads(child.stdout.readline())
        assert owner["acquired"]
        with process_lock(path) as acquired:
            assert not acquired
        os.kill(owner["pid"], signal.SIGTERM)
        child.wait(timeout=10)
        with process_lock(path) as acquired:
            assert acquired
    finally:
        if child.poll() is None:
            if owner:
                os.kill(owner["pid"], signal.SIGTERM)
            child.terminate()
            child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()


def test_second_supervisor_exits_without_starting_components(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "runtime")
    with process_lock(store.root / "supervisor.lock") as acquired:
        assert acquired
        result = subprocess.run([sys.executable, "-m", "gov_mcp.knowledge_service", "--runtime", str(store.root)],
                                capture_output=True, timeout=15, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0 and "Another supervisor" in result.stdout
    assert not (store.root / "service-state.json").exists()


def test_broken_os_recovery_metadata_does_not_break_read_only_knowledge_status(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "runtime")
    (store.root / "startup-registration.json").write_text('{"installed":true}', encoding="utf-8")
    (store.root / "startup-recovery-status.json").write_text('{broken fixture', encoding="utf-8")
    before = store.path.read_bytes()
    status = KnowledgeStore(store.root, read_only=True).status()
    assert status["startup"]["registration"]["installed"] is True
    assert status["startup"]["recovery"]["status"] == "unreadable"
    assert status["counts"]["heads"] == 0 and store.path.read_bytes() == before

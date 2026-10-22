"""Recovery guards must remain read-only and refuse paid or unpaused workflows."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location("inspect_agent_workflow", Path(__file__).parents[1] / "scripts/inspect_agent_workflow.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.mark.parametrize("paused,inference,form,safe", [(True,False,False,True),(False,False,False,False),(True,True,False,False),(True,False,True,False)])
def test_recovery_reads_only_existing_business_state(tmp_path, paused, inference, form, safe):
    config = tmp_path / "config/decision/connections.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"inference_enabled": inference, "form_verifier_enabled": form}))
    path = tmp_path / ".runtime/agent-workflow/knowledge.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE state(key TEXT,value TEXT); CREATE TABLE wf_versions(id TEXT);")
        db.execute("INSERT INTO state VALUES('wf_control',?)", (json.dumps({"paused":paused,"heartbeat_at":recovery.time.time()}),))
        db.execute("INSERT INTO state VALUES('wf_head','\"head\"')")
    before = path.read_bytes()
    result = recovery.inspect(tmp_path)
    assert result['safe_to_recover'] is safe
    assert result['heartbeat_fresh'] and result['model_calls'] == 0
    assert path.read_bytes() == before


def test_recovery_never_creates_missing_runtime(tmp_path):
    config = tmp_path / "config/decision/connections.json"
    config.parent.mkdir(parents=True)
    config.write_text('{}')
    with pytest.raises(sqlite3.OperationalError): recovery.inspect(tmp_path)
    assert not (tmp_path / '.runtime').exists()

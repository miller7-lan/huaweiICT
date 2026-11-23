"""The public receipt exposes run status and known tool names."""
import json
from types import SimpleNamespace

import run_frozen_qa_native as runner


def test_public_receipt_omits_private_content():
    report = {"passed": True, "question": "private synthetic question",
              "final_answer": "private synthetic answer", "conversation_id": "internal",
              "usage_events": [{"detail": "private synthetic event"}],
              "tool_order": ["govkb_flow_open", "private synthetic tool", None, "govkb_flow_submit"],
              "model_starts": 2, "events": 9}
    public = runner.public_receipt(report)
    assert public == {"passed": True, "tool_order": ["govkb_flow_open", "govkb_flow_submit"],
                      "other_tool_events": 2, "model_starts": 2, "events": 9}
    assert "private" not in json.dumps(public)


def test_run_keeps_its_full_receipt_in_private_runtime(tmp_path, monkeypatch, capsys):
    private_dir, public_dir = tmp_path / "private", tmp_path / "evidence"
    private_dir.mkdir()
    monkeypatch.setattr(runner, "RUNTIME", private_dir)
    monkeypatch.setattr(runner, "OUTPUT", public_dir)
    monkeypatch.setattr(runner.sys, "argv", ["run_frozen_qa_native", "synthetic question"])
    monkeypatch.setattr(runner, "read", lambda path: {"developer": {}, "agents": {"assistant": 42}})

    def fail(_credentials):
        raise OSError("synthetic private error detail")

    monkeypatch.setattr(runner, "NexentAPI", lambda: SimpleNamespace(login=fail, close=lambda: None))
    runner.main()

    private = json.loads(next(private_dir.glob("*-private.json")).read_text("utf-8"))
    public = json.loads(next(public_dir.glob("*.json")).read_text("utf-8"))
    assert private["question"] == "synthetic question"
    assert "question" not in public and "conversation_id" not in public
    assert public["passed"] is False and public["error"] == "OSError"
    assert json.loads(capsys.readouterr().out) == public

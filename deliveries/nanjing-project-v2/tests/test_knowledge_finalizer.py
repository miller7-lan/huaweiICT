"""An unavailable platform must leave current evidence and keep the gate failed."""

import json

import httpx
import pytest

import finalize_knowledge_intake as finalizer
from gov_mcp.knowledge import KnowledgeStore


def test_unavailable_platform_records_failure_without_relabeling_old_success(tmp_path, monkeypatch):
    KnowledgeStore.initialize(tmp_path / ".runtime/knowledge-update")
    evidence = tmp_path / "evidence/knowledge-update"
    evidence.mkdir(parents=True)
    historical = evidence / "mcp-connection-2026-10-15.json"
    old_result = b'{"platform_catalog_verified": true, "historical": true}'
    historical.write_bytes(old_result)
    (evidence / "regression-tests.xml").write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>', encoding="utf-8")
    accounts = tmp_path / ".runtime/m2/accounts.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps({"developer": {"password": "synthetic-private-password"}}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("shareable synthetic notes", encoding="utf-8")
    closed = []

    class UnavailableAPI:
        def login(self, credentials):
            raise httpx.ConnectError("synthetic-private-password must not enter the public report")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(finalizer, "ROOT", tmp_path)
    monkeypatch.setattr(finalizer, "NexentAPI", UnavailableAPI)
    monkeypatch.setattr(finalizer.subprocess, "check_output", lambda *args, **kwargs: b"notes.txt\0")
    with pytest.raises(AssertionError, match="platform catalog"):
        finalizer.main()

    report_text = (evidence / "intake-step-status.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["tests_passed"] is True and report["test_count"] == 1
    assert report["platform_catalog_verified"] is False
    assert report["platform_catalog_failure_class"] == "ConnectError"
    assert report["platform_catalog_failure_stage"] == "login"
    assert report["official_knowledge_published"] == 0
    assert "synthetic-private-password" not in report_text
    assert historical.read_bytes() == old_result and closed == [True]

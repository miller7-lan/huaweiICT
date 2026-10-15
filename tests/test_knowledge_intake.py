"""Fresh-intake safety with a fake fetcher; no real source content or requests."""

import json
import sys
from pathlib import Path

import pytest

from gov_mcp.knowledge import KnowledgeStore


def test_first_intake_is_disabled_unpublished_and_cannot_overwrite_an_existing_source(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import intake_henan_sources as command
    runtime = tmp_path / "runtime"
    store = KnowledgeStore.initialize(runtime / "knowledge-update")
    (tmp_path / "evidence/knowledge-update").mkdir(parents=True)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "RUNTIME", runtime)
    calls = []
    class FakeFetcher:
        def __init__(self, store):
            pass
        def fetch(self, config, url):
            calls.append(url)
            assert config["enabled"] is True
            return 200, {"content-type": "text/html"}, b"<p>self-authored intake test only, not a policy</p>", url
    monkeypatch.setattr(command, "Fetcher", FakeFetcher)
    monkeypatch.setattr(sys, "argv", ["intake", "--source", "norm-publisher"])
    command.main()
    status = store.status()
    assert status["counts"]["documents"] == status["counts"]["heads"] == 0
    assert status["sites"][0]["enabled"] is False
    with pytest.raises(ValueError, match="already exists"):
        command.main()
    assert len(calls) == 1
    report = json.loads((tmp_path / "evidence/knowledge-update/henan-norm-publisher-intake.json").read_text(encoding="utf-8"))
    assert report["body_acquired"] and not report["body_ingested"] and not report["business_review_completed"]
    monkeypatch.setattr(sys, "argv", ["intake", "--source", "norm-publisher", "--validate-only"])
    command.main()
    assert len(calls) == 1 and not store.status()["sites"][0]["enabled"]


def test_pdf_intake_refuses_wrong_header_without_ingesting_or_enabling(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import intake_henan_sources as command
    runtime = tmp_path / "runtime"
    store = KnowledgeStore.initialize(runtime / "knowledge-update")
    (tmp_path / "evidence/knowledge-update").mkdir(parents=True)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "RUNTIME", runtime)
    class FakeFetcher:
        def __init__(self, store):
            pass
        def fetch(self, config, url):
            return 200, {"content-type": "application/pdf"}, b"<html>not a PDF</html>", url
    monkeypatch.setattr(command, "Fetcher", FakeFetcher)
    monkeypatch.setattr(sys, "argv", ["intake", "--source", "validity-pdf"])
    with pytest.raises(SystemExit):
        command.main()
    status = store.status()
    assert status["counts"]["documents"] == 0
    assert status["sites"][0]["enabled"] is False and "header" in status["sites"][0]["paused_reason"]
    report = json.loads((tmp_path / "evidence/knowledge-update/henan-validity-pdf-intake.json").read_text(encoding="utf-8"))
    assert not report["body_acquired"] and not report["body_ingested"]

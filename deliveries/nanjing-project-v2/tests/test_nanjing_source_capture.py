"""These fixtures test collection boundaries, not policy truth or live availability."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/capture_nanjing_rule_sources.py"
spec = importlib.util.spec_from_file_location("nanjing_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private", "https://example.com/",
    capture.SOURCES[0][1] + "?token=private",
    "https://user:password@gaj.nanjing.gov.cn/njsgaj/202505/t20250529_5573116.html",
])
def test_non_allowlisted_destination_stops_before_network(monkeypatch, url):
    monkeypatch.setattr(capture.urllib.request, "build_opener",
        lambda *a: pytest.fail("No network opener should be created"))
    with pytest.raises(capture.AccessStopped):
        capture.fixed_request(url, {})


def test_exclusive_attempt_marker_prevents_second_campaign(monkeypatch, tmp_path):
    (tmp_path / "attempt.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(capture.KnowledgeStore, "initialize",
        lambda *a: pytest.fail("No store/network after existing marker"))
    with pytest.raises(FileExistsError):
        capture.collect(runtime=tmp_path)


def test_robots_denial_stops_origin_without_trying_second_document(monkeypatch, tmp_path):
    calls = []
    def request(url, headers):
        calls.append(url)
        return 403, {}, b"denied"
    monkeypatch.setattr(capture, "fixed_request", request)
    result = capture.collect(sources=capture.SOURCES[1:3], runtime=tmp_path)
    assert len(calls) == 1 and calls[0].endswith("/robots.txt")
    assert [s["status"] for s in result["sources"]] == ["stopped", "skipped_after_origin_stop"]
    assert result["sources"][0]["access_failure"] == {"phase": "robots", "http_status": 403}


def test_html_capture_preserves_raw_without_review_or_publication(monkeypatch, tmp_path):
    raw = "<html><p>公开测试原文，不是已批准政策。</p></html>".encode()
    monkeypatch.setattr(capture, "fixed_request",
        lambda url, headers: (404, {}, b"") if url.endswith("/robots.txt")
        else (200, {"content-type": "text/html"}, raw))
    monkeypatch.setattr("gov_mcp.knowledge_fetch.time.sleep", lambda *_: None)
    result = capture.collect(sources=(capture.SOURCES[0],), runtime=tmp_path)
    record = result["sources"][0]
    assert record["status"] == "captured_pending_review"
    assert (tmp_path / record["filename"]).read_bytes() == raw
    assert len(record["requests"]) == 2
    assert not result["human_business_review"] and not result["knowledge_published"]
    assert result["model_calls"] == 0
    with sqlite3.connect(tmp_path / "capture-store/knowledge.sqlite3") as db:
        assert all(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
                   for table in ("reviews", "versions", "heads"))


def test_html_disguised_as_pdf_does_not_get_saved_as_original(monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "fixed_request",
        lambda url, headers: (404, {}, b"") if url.endswith("/robots.txt")
        else (200, {"content-type": "text/html"}, b"<html>login</html>"))
    monkeypatch.setattr("gov_mcp.knowledge_fetch.time.sleep", lambda *_: None)
    result = capture.collect(sources=(capture.SOURCES[-1],), runtime=tmp_path)
    assert result["sources"][0]["status"] == "stopped"
    assert not list(tmp_path.glob("*.pdf"))


def test_new_update_cli_selects_only_four_existing_html_urls(monkeypatch, tmp_path):
    import sys
    called = []
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--collect", "--group", "matter-html", "--runtime", str(tmp_path)])
    monkeypatch.setattr(capture, "collect", lambda **kwargs: called.append(kwargs) or
        {"sources": [], "model_calls": 0, "knowledge_published": False})
    capture.main()
    assert called[0]["runtime"] == tmp_path
    assert len(called[0]["sources"]) == 4 and all(s[3] == "html" for s in called[0]["sources"])
    assert not any(s[0].startswith("NPC") for s in called[0]["sources"])

"""Synthetic OCR protocol/worker tests; the real Windows scan run is recorded separately."""

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

PdfWriter = pytest.importorskip("pypdf", reason="Install the knowledge extra for OCR/PDF tests").PdfWriter

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import schedule, validate_config, worker_once
from gov_mcp.knowledge_pdf import checked_text, extract_document, validate_ocr


OCR = {"engine": "windows.media.ocr", "language": "zh-Hans-CN"}


def payload(sha, count=1):
    return {"schema": 1, "engine": OCR["engine"], "language": OCR["language"], "input_sha256": sha,
        "confidence_available": False, "requires_visual_review": True,
        "pages": [{"page": i+1, "text": "这 是 纯 模 拟 身 份 证 文 本 ， 不 是 官 方 政 策 。", "line_count": 1, "width": 800, "height": 1100} for i in range(count)]}


def blank_pdf(path, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=280)
    writer.write(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scans_require_explicit_ocr_and_preserve_raw_recognition(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-scan.pdf"
    sha = blank_pdf(path)
    calls = []
    def recognize(path, language):
        calls.append(language)
        return payload(sha)
    monkeypatch.setattr("gov_mcp.knowledge_pdf.windows_ocr", recognize)
    with pytest.raises(ValueError, match="explicit OCR"):
        extract_document(path)
    assert not calls
    result = extract_document(path, OCR)
    assert calls == ["zh-Hans-CN"]
    assert "模拟身份证文本" in result["chunks"][0]["text"]
    assert "身 份 证" in result["extraction"]["raw_ocr_pages"][0]["text"]
    assert result["chunks"][0]["locator"] == "PDF第1页（OCR待核对）"
    assert result["extraction"]["requires_visual_review"] is True
    assert result["extraction"]["confidence_available"] is False


@pytest.mark.parametrize("field,value", [("input_sha256", "different"), ("language", "en-US"), ("pages", []),
    ("confidence_available", True), ("requires_visual_review", False), ("schema", True)])
def test_ocr_protocol_rejects_incomplete_or_unbound_output(field, value):
    result = payload("synthetic-hash")
    result[field] = value
    with pytest.raises(ValueError, match="bind"):
        validate_ocr(result, 1, "synthetic-hash", "zh-Hans-CN")


@pytest.mark.parametrize("field,value", [("page", 2), ("line_count", [1, 1]), ("line_count", 801), ("width", 4800), ("height", 0), ("text", "")])
def test_ocr_page_bounds_and_actual_sequence_are_checked(field, value):
    result = payload("synthetic-hash")
    result["pages"][0][field] = value
    with pytest.raises(ValueError):
        validate_ocr(result, 1, "synthetic-hash", "zh-Hans-CN")


def test_spaced_fullwidth_possible_personal_identifier_is_quarantined():
    with pytest.raises(ValueError, match="personal identifier"):
        checked_text("纯模拟输入 " + " ".join("１２３４５６７８９０１２３４５６７Ｘ"))


def test_large_scan_does_not_launch_ocr(tmp_path, monkeypatch):
    path = tmp_path / "too-many-pages.pdf"
    blank_pdf(path, 21)
    monkeypatch.setattr("gov_mcp.knowledge_pdf.windows_ocr", lambda *args: pytest.fail("Oversized scan must not launch OCR"))
    with pytest.raises(ValueError, match="20 pages"):
        extract_document(path, OCR)


@pytest.mark.parametrize("ocr", [True, {}, {"engine": "unknown"}, {"engine": "windows.media.ocr", "language": "en-US"}])
def test_unsupported_source_ocr_configuration_is_rejected(ocr):
    config = source()
    config["pdf_ocr"] = ocr
    with pytest.raises(ValueError, match="OCR"):
        validate_config(config)


def source():
    return {"id": "synthetic-ocr", "label": "模拟扫描源", "origin": "https://fixtures.invalid",
        "allowed_paths": ["/scan.pdf"], "seeds": ["https://fixtures.invalid/scan.pdf"], "enabled": True,
        "interval_seconds": 86400, "delay_seconds": 5, "pdf_ocr": OCR,
        "metadata": {"title": "模拟扫描件", "region": "unknown", "matter": "unclassified", "document_type": "synthetic",
            "effective_from": None, "effective_to": None, "usage_basis": "Self-authored OCR fixture only", "scope_confirmed": False},
        "access_review": {"approved": True, "reviewer": "synthetic", "usage_basis": "No network request",
            "checked_at": datetime.now(timezone.utc).isoformat()}}


def test_worker_uses_ocr_lease_and_protocol_without_publishing(tmp_path, monkeypatch):
    store = KnowledgeStore.initialize(tmp_path / "store")
    config = source()
    store.register(config)
    now = time.time()
    raw = b"%PDF-synthetic-protocol-fixture-not-a-real-scan"
    report = validate_ocr(payload(hashlib.sha256(raw).hexdigest()), 1, hashlib.sha256(raw).hexdigest(), "zh-Hans-CN")
    class Fetcher:
        def fetch(self, config, url):
            return 200, {"content-type": "application/pdf"}, raw, url
    def parse(command, **options):
        assert command[-1] == "--windows-ocr" and options["timeout"] == 150
        with store.db() as db:
            assert db.execute("SELECT lease_until FROM jobs").fetchone()[0] == now+600
        return SimpleNamespace(returncode=0, stdout=json.dumps(report).encode())
    monkeypatch.setattr(subprocess, "run", parse)
    schedule(store, now=now)
    worker_once(store, Fetcher(), now=now)
    status = store.status()
    assert status["counts"]["documents"] == 1 and status["counts"]["heads"] == 0
    meta = status["pending_review"][0]["metadata"]
    assert meta["effective_from"] is None and meta["scope_confirmed"] is False
    assert meta["source_observation"]["pdf_extraction"] == report["extraction"]

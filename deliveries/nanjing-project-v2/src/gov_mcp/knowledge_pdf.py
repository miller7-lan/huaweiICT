"""Bounded PDF extraction; optional local Windows OCR always remains pending review."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from pypdf import PdfReader


def checked_text(text):
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    if not 8 <= len(text.strip()) <= 16000 or "\x00" in text or re.search(r"\d{17}[\dXx]", compact):
        raise ValueError("Missing text, oversized page or possible personal identifier")
    return text.strip()


def windows_ocr(path, language):
    if sys.platform != "win32" or not shutil.which("powershell.exe"):
        raise ValueError("Explicit Windows native OCR runtime unavailable")
    if language != "zh-Hans-CN":
        raise ValueError("Only explicitly supported Simplified Chinese OCR is enabled")
    try:
        result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-File",
            str(Path(__file__).with_name("knowledge_ocr_windows.ps1")), "-InputPath", str(Path(path).resolve()), "-Language", language],
            capture_output=True, timeout=90, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Windows OCR subprocess timed out") from error
    if result.returncode or len(result.stdout) > 5*1024*1024:
        raise ValueError("Windows native OCR failed; inspect the private input and installed language")
    return json.loads(result.stdout.decode("utf-8-sig"))


def validate_ocr(result, page_count, sha256, language):
    if (not isinstance(result, dict) or type(result.get("schema")) is not int or result["schema"] != 1 or result.get("engine") != "windows.media.ocr"
            or result.get("language") != language or result.get("input_sha256") != sha256
            or result.get("confidence_available") is not False or result.get("requires_visual_review") is not True
            or not isinstance(result.get("pages"), list) or len(result["pages"]) != page_count):
        raise ValueError("OCR output does not bind the requested input, language and complete page set")
    chunks, pages = [], []
    for index, page in enumerate(result["pages"]):
        if (not isinstance(page, dict) or set(page) != {"page", "text", "line_count", "width", "height"}
                or type(page.get("page")) is not int or page["page"] != index+1
                or not isinstance(page.get("text"), str) or type(page.get("line_count")) is not int or not 1 <= page["line_count"] <= 800
                or any(type(page.get(key)) is not int or not 1 <= page[key] <= 2400 for key in ("width", "height"))):
            raise ValueError("Invalid OCR page sequence, geometry or text")
        text = checked_text(page["text"])
        # Preserve raw recognition separately; only remove spacing between CJK
        # characters to make the derived candidate searchable. No glyph repair.
        normalized = re.sub(r"(?<=[\u3400-\u9fff])[^\S\r\n]+(?=[\u3400-\u9fff])", "", text)
        chunks.append({"locator": f"PDF第{index+1}页（OCR待核对）", "category": "procedure", "text": normalized})
        pages.append({**page, "text": text})
    provenance = {"method": "windows_media_ocr", "algorithm": "winrt-pdf-ocr-v1", "language": language,
        "input_sha256": sha256, "requires_visual_review": True, "confidence_available": False,
        "text_normalization": "cjk_intercharacter_space_fold_v1", "raw_ocr_pages": pages,
        "rendering_scope": "Source page order and visible PDF CropBox; full MediaBox booklet spreads are not combined"}
    return {"schema": 1, "chunks": chunks, "extraction": provenance}


def extract_document(path, ocr=None):
    path = Path(path)
    if ocr is not None and ocr != {"engine": "windows.media.ocr", "language": "zh-Hans-CN"}:
        raise ValueError("Supported OCR configuration required")
    if not 1 <= path.stat().st_size <= 5*1024*1024:
        raise ValueError("Bounded PDF file required")
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
        raise ValueError("Encrypted or oversized PDF")
    texts = [page.extract_text() or "" for page in reader.pages]
    if all(len(text.strip()) >= 8 for text in texts):
        return {"schema": 1, "chunks": [{"locator": f"PDF第{index+1}页", "category": "procedure", "text": checked_text(text)}
            for index, text in enumerate(texts)], "extraction": {"method": "pdf_text_layer", "requires_visual_review": False}}
    if not ocr:
        raise ValueError("Missing text layer; explicit OCR source configuration required")
    if ocr != {"engine": "windows.media.ocr", "language": "zh-Hans-CN"} or len(reader.pages) > 20:
        raise ValueError("Supported OCR configuration and at most 20 pages required")
    # Do not use an incomplete text layer or silently merge it with OCR output.
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    result = windows_ocr(path, ocr["language"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError("PDF changed during OCR")
    return validate_ocr(result, len(reader.pages), sha256, ocr["language"])


def extract(path):
    """Compatibility API: text-layer extraction only, with no automatic OCR."""
    return extract_document(path)["chunks"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--windows-ocr", action="store_true")
    args = parser.parse_args()
    config = {"engine": "windows.media.ocr", "language": "zh-Hans-CN"} if args.windows_ocr else None
    print(json.dumps(extract_document(args.path, config), ensure_ascii=True))

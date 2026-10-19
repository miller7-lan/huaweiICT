"""Consume the existing team's Windows OCR contract into an unreviewed registry."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from gov_mcp.knowledge_pdf import validate_ocr

EXPECTED_SHA256 = "95e8decc4bb5aecb5324b7cea311659d26245998b0c6d24049577c8e267220bd"
EXPECTED_URL = "https://www.pdsxcq.gov.cn/upload/files/2023/6/353f9d72ee36b1751de857cf71917117.pdf"
EXPECTED_REQUEST = "nanjing-law-gazette-ocr-v1"


def check_original(request, raw):
    if (request.get("schema") != 1 or request.get("request_id") != EXPECTED_REQUEST
        or request.get("source_url") != EXPECTED_URL or request.get("input_sha256") != EXPECTED_SHA256
        or hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256 or len(raw) != request.get("input_bytes")
        or request.get("pdf_page_count") != 4 or request.get("requested_pdf_pages") != [1, 2, 3, 4]
        or request.get("language") != "zh-Hans-CN"):
        raise ValueError("OCR交接未绑定已归档的四页官方原件")


def consume(request, original, response, store):
    """Validate all pages, preserve original recognition and ingest without a review call."""
    check_original(request, original)
    parsed = validate_ocr(response, 4, EXPECTED_SHA256, request["language"])
    with store.db() as db:
        if any(db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("reviews", "versions", "heads")):
            raise ValueError("OCR交接只能进入隔离的未发布候选库")
    checked = datetime.now(timezone.utc).isoformat()
    origin = "https://" + urlsplit(EXPECTED_URL).netloc
    site_id = "NJ-LAW-GAZETTE-OCR-HANDOFF"
    basis = "Previously archived official public statutory text; downstream OCR contract integration; no applicant data"
    store.register({"id": site_id, "label": "全国人大公报扫描原件OCR交接", "enabled": False,
        "origin": origin, "allowed_paths": [urlsplit(EXPECTED_URL).path], "seeds": [EXPECTED_URL],
        "interval_seconds": 86400, "delay_seconds": 5,
        "pdf_ocr": {"engine": "windows.media.ocr", "language": request["language"]},
        "metadata": {"title": "居民身份证法公报扫描原件OCR待复核", "region": "national",
            "matter": "resident-id-expiry-renewal", "document_type": "statutory_gazette_ocr_pending",
            "usage_basis": basis, "scope_confirmed": False},
        "access_review": {"approved": True, "reviewer": "archived_public_source_technical_screen",
            "actor_kind": "machine", "checked_at": checked, "usage_basis": basis, "business_review_completed": False}})
    metadata = {"title": "居民身份证法公报四页OCR（待视觉/业务复核）", "region": "national",
        "matter": "resident-id-expiry-renewal", "document_type": "statutory_gazette_ocr_pending",
        "published_at": None, "effective_from": None, "effective_to": None, "usage_basis": basis,
        "source_observation": {"media_type": "application/pdf", "raw_sha256": EXPECTED_SHA256,
            "pdf_extraction": parsed["extraction"]},
        "ocr_handoff": {"request_id": EXPECTED_REQUEST, "input_sha256": EXPECTED_SHA256,
            "business_review": "pending", "recognition_errors_must_not_be_repaired_by_template": True}}
    revision = store.ingest(site_id, EXPECTED_URL, original, metadata, parsed["chunks"])
    with store.db() as db:
        row, chunks = store._content(db, revision)
        if row["sha256"] != EXPECTED_SHA256 or [r["locator"] for r in chunks] != [c["locator"] for c in parsed["chunks"]]:
            raise ValueError("OCR下游原件或页序保存不一致")
        counts = {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("reviews", "versions", "heads")}
        if any(counts.values()): raise ValueError("OCR交接库不能带入人工批准或发布头")
    return {"status": "OCR_CANDIDATE_INGESTED_PENDING_VISUAL_REVIEW", "request_id": EXPECTED_REQUEST,
        "revision_id": revision, "input_sha256": EXPECTED_SHA256, "pages": len(parsed["chunks"]),
        "engine": "windows.media.ocr", "confidence_available": False, "requires_visual_review": True,
        "review_version_head_counts": counts, "automatic_monitor_enabled": False,
        "human_business_review": False, "formal_publication": False, "candidate": parsed}

"""Bounded DeepSeek OCR adapter and pure vision/file request builders.

No call happens on import or plan creation. Live OCR requires an explicit
per-file grant and budget, supplied only after the user's external-upload
approval. Results always remain unreviewed machine candidates, never originals.
"""
import base64
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from gov_mcp.deepseek import PROFILES, load_key

PURPOSES = ("text_recognition", "rich_layout_recognition")
OCR_URL = "https://api.deepseek.com/v1/tools/ocr"


def upload_plan(path, source_id, source_url, original_sha256, original_page, purpose):
    path = Path(path)
    if purpose not in PURPOSES:
        raise ValueError("UNSUPPORTED_RECOGNITION_PURPOSE")
    if not re.fullmatch(r"[0-9a-f]{64}", original_sha256) or not source_id or not source_url.startswith("https://"):
        raise ValueError("ORIGINAL_SOURCE_BINDING_REQUIRED")
    if type(original_page) is not int or original_page < 1:
        raise ValueError("ORIGINAL_PAGE_NUMBER_REQUIRED")
    raw = path.read_bytes()
    if not 1 <= len(raw) <= 10 * 1024 * 1024:
        raise ValueError("SCAN_IMAGE_SIZE_LIMIT")
    mime = "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None
    if mime is None:
        raise ValueError("PAGE_IMAGE_REQUIRED_NO_UNCOUNTED_DOCUMENT_UPLOAD")
    return {"endpoint": OCR_URL, "purpose": purpose, "filename": path.name, "mime": mime,
            "upload_sha256": sha256(raw).hexdigest(), "bytes": len(raw), "recognition_pages": 1,
            "source_id": source_id, "source_url": source_url, "original_sha256": original_sha256,
            "original_page": original_page, "requires_user_upload_approval": True}


@dataclass(frozen=True)
class UploadGrant:
    approval_reference: str
    upload_sha256: str
    purpose: str
    public_non_sensitive_confirmed: bool
    plan_sha256: str


def plan_fingerprint(plan):
    return sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Budget:
    max_calls: int
    max_pages: int
    max_usd: float
    reserved_usd_per_page: float
    calls: int = 0
    pages: int = 0

    def reserve(self, pages):
        if (type(self.max_calls) is not int or type(self.max_pages) is not int or self.max_calls < 1 or self.max_pages < 1
                or type(pages) is not int or pages < 1 or type(self.max_usd) not in (int,float)
                or type(self.reserved_usd_per_page) not in (int,float)
                or not math.isfinite(self.max_usd) or not math.isfinite(self.reserved_usd_per_page)
                or self.max_usd <= 0 or self.reserved_usd_per_page <= 0):
            raise ValueError("EXPLICIT_VALID_CALL_PAGE_AND_COST_BUDGET_REQUIRED")
        if (self.calls + 1 > self.max_calls or self.pages + pages > self.max_pages
                or (self.pages + pages) * self.reserved_usd_per_page > self.max_usd):
            raise ValueError("OCR_BUDGET_EXHAUSTED")
        # Reserve before sending, including failures/timeouts that could still be billed.
        self.calls += 1
        self.pages += pages


def parse_ocr(payload, plan):
    if not isinstance(payload, dict) or payload.get("object") != "ocr.result" or not isinstance(payload.get("id"), str) \
            or not payload["id"].startswith("ocr-") or payload.get("purpose") != plan["purpose"]:
        raise ValueError("OCR_RESPONSE_BINDING_INVALID")
    pages = payload.get("pages")
    if not isinstance(pages, list) or type(payload.get("page_count")) is not int \
            or payload["page_count"] != len(pages) or len(pages) != plan["recognition_pages"]:
        raise ValueError("OCR_PAGE_COUNT_MISMATCH")
    result = []
    for index, page in enumerate(pages):
        if (not isinstance(page, dict) or type(page.get("index")) is not int or page["index"] != index
                or type(page.get("text")) is not str or page.get("finish_reason") not in ("stop", "length")):
            raise ValueError("OCR_PAGE_SCHEMA_INVALID")
        result.append({"source_id": plan["source_id"], "source_url": plan["source_url"],
                       "original_sha256": plan["original_sha256"], "original_page": plan["original_page"],
                       "filename": plan["filename"], "upload_sha256": plan["upload_sha256"],
                       "recognition_id": payload["id"], "purpose": plan["purpose"], "text": page["text"],
                       "finish_reason": page["finish_reason"], "complete": page["finish_reason"] == "stop" and bool(page["text"].strip()),
                       "evidence_kind": "machine_ocr_unreviewed", "human_reviewed": False,
                       "current_applicability": "unknown", "formal_approval": False,
                       "business_conclusion_status": "PAUSED_UNREVIEWED"})
    return result


def recognize(path, plan, grant, budget, *, client=None, environment=None):
    if not isinstance(grant, UploadGrant) or type(grant.approval_reference) is not str \
            or not grant.approval_reference.strip() or grant.public_non_sensitive_confirmed is not True:
        raise PermissionError("EXPLICIT_PER_FILE_USER_UPLOAD_APPROVAL_REQUIRED")
    if plan.get("endpoint") != OCR_URL or plan.get("purpose") not in PURPOSES or plan.get("recognition_pages") != 1:
        raise ValueError("INVALID_OCR_PLAN")
    path = Path(path)
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != plan["upload_sha256"] or grant.upload_sha256 != plan["upload_sha256"] \
            or grant.purpose != plan["purpose"] or grant.plan_sha256 != plan_fingerprint(plan):
        raise PermissionError("APPROVAL_OR_UPLOAD_FINGERPRINT_CHANGED")
    key = load_key(PROFILES["official"], environment)
    if not key:
        raise ValueError("WAITING_DEEPSEEK_CREDENTIAL")
    budget.reserve(1)

    def send(http):
        try:
            response = http.post(OCR_URL, headers={"Authorization": "Bearer " + key},
                                 data={"purpose": plan["purpose"]},
                                 files={"file": (plan["filename"], raw, plan["mime"])})
        except Exception:
            raise ValueError("OCR_TRANSPORT_FAILURE_POSSIBLY_BILLED_NO_RETRY") from None
        if response.status_code != 200:
            raise ValueError("OCR_HTTP_FAILURE_NO_AUTOMATIC_RETRY")
        try:
            payload = response.json()
        except Exception:
            raise ValueError("OCR_INVALID_JSON_NO_RETRY") from None
        return parse_ocr(payload, plan)
    if client is not None:
        return send(client)
    import httpx
    with httpx.Client(timeout=180, verify=True, trust_env=True, follow_redirects=False) as http:
        return send(http)


def vision_message(image_bytes, mime, question):
    if mime not in ("image/png", "image/jpeg") or type(question) is not str or not question.strip():
        raise ValueError("IMAGE_TYPE_AND_QUESTION_REQUIRED")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise ValueError("SCAN_IMAGE_SIZE_LIMIT")
    data = base64.b64encode(image_bytes).decode("ascii")
    return {"role": "user", "content": [{"type": "text", "text": question},
             {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "original"}}]}


def file_qa_message(file_id, question):
    if not isinstance(file_id, str) or not file_id.startswith("file-api-") or not question.strip():
        raise ValueError("OFFICIAL_FILE_REFERENCE_AND_QUESTION_REQUIRED")
    return {"role": "user", "content": [{"type": "file", "file_id": file_id}, {"type": "text", "text": question}]}

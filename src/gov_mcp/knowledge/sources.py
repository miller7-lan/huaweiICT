"""Bounded fetching from individually approved URLs and deterministic extraction."""

import csv
from html.parser import HTMLParser
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

from .store import KBError, digest


class BodyParser(HTMLParser):
    def __init__(self, selector):
        super().__init__(convert_charrefs=True)
        self.selector, self.stack, self.active, self.matches, self.parts = selector, [], None, 0, []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        match = tag == self.selector.get("tag") and all(values.get(k) == v for k, v in self.selector.items() if k != "tag")
        if tag in {"br", "hr", "img", "meta", "link", "input", "source", "wbr"}:
            if self.active is not None and tag == "br": self.parts.append("\n")
            return
        self.stack.append(tag)
        if match:
            self.matches += 1
            if self.active is None: self.active = len(self.stack)
        if self.active is not None and tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}: self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag not in self.stack: return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.active is not None:
            self.parts.append("\n")
            if index < self.active: self.active = None
        self.stack = self.stack[:index]

    def handle_data(self, data):
        if self.active is not None and not any(tag in {"script", "style", "nav", "header", "footer", "aside"} for tag in self.stack):
            self.parts.append(data)

    def text(self):
        lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        text = "\n".join(line for line in lines if line)
        if self.matches != 1 or not text: raise KBError("BODY_NOT_FOUND")
        if re.search(r"请输入验证码|安全验证|访问过于频繁|captcha", text, re.I): raise KBError("CHALLENGE_PAGE")
        return text


FORMATS = {"html", "txt", "csv", "png", "jpg", "jpeg"}


def check_source(source, synthetic=False):
    if not source.get("approved") or not source.get("enabled"):
        raise KBError("SOURCE_NOT_APPROVED")
    if source.get("data_class") != "public" and not (synthetic and source.get("data_class") == "synthetic"):
        raise KBError("DATA_CLASS_BLOCKED")
    if not isinstance(source.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", source["id"]):
        raise KBError("INVALID_SOURCE_ID")
    selector = source.get("body_selector", {})
    if source.get("format") == "html" and (not selector.get("tag") or set(selector)-{"tag", "id", "class"}):
        raise KBError("INVALID_BODY_SELECTOR")


class Fetcher:
    def __init__(self, store, max_bytes=20*1024*1024, client=None, synthetic=False, resolver=socket.getaddrinfo, ocr=None):
        self.store, self.max_bytes = store, max_bytes
        self.client, self.synthetic, self.resolver, self.ocr = client, synthetic, resolver, ocr

    def allowed(self, source, url):
        parsed = urlparse(url)
        registered = [source["url"]] + [a["url"] for a in source.get("attachment_urls", [])]
        if url not in registered or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.fragment:
            raise KBError("URL_NOT_REGISTERED")
        if parsed.hostname not in source.get("allowed_hosts", []): raise KBError("HOST_NOT_ALLOWED")
        if self.synthetic and source["data_class"] == "synthetic": return
        if parsed.scheme != "https" or not (parsed.hostname == "gov.cn" or parsed.hostname.endswith(".gov.cn")):
            raise KBError("OFFICIAL_HTTPS_SOURCE_REQUIRED")
        try:
            addresses = self.resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
                raise KBError("NON_PUBLIC_ADDRESS")
        except OSError:
            raise KBError("SOURCE_DNS_FAILED") from None

    def parse(self, source, resource, raw, content_type):
        fmt = resource["format"]
        pieces = []
        if fmt not in FORMATS: raise KBError("UNSUPPORTED_FORMAT")
        if fmt in {"html", "txt", "csv"}:
            try: text = raw.decode(resource.get("encoding", "utf-8-sig"))
            except (LookupError, UnicodeError): raise KBError("UNSUPPORTED_ENCODING") from None
            if fmt == "html":
                parser = BodyParser(source["body_selector"])
                parser.feed(text)
                text = parser.text()
            elif "html" in content_type or re.search(r"<html|<!doctype", text, re.I):
                raise KBError("UNEXPECTED_HTML")
            if fmt == "csv":
                reader = csv.DictReader(io.StringIO(text, newline=""))
                if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames): raise KBError("INVALID_CSV")
                for i, row in enumerate(reader, 1):
                    if None in row or any(v is None for v in row.values()): raise KBError("INVALID_CSV")
                    pieces.append(("；".join(f"{k}：{v}" for k, v in row.items()), {"kind": "csv_record", "record_number": i, "columns": reader.fieldnames}))
            else:
                for i, line in enumerate(text.splitlines(), 1):
                    if line.strip(): pieces.append((line.strip(), {"kind": "html_paragraph" if fmt == "html" else "line", "line": i}))
        else:
            if self.ocr is not None:
                rows = self.ocr(raw)
            else:
                from PIL import Image
                from rapidocr_onnxruntime import RapidOCR
                import numpy as np
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 25000000: raise KBError("IMAGE_TOO_LARGE")
                    data = np.asarray(image.convert("RGB"))[:, :, ::-1]
                engine = RapidOCR(det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                    det_intra_op_num_threads=1, cls_intra_op_num_threads=1, rec_intra_op_num_threads=1)
                rows, _ = engine(data)
            for polygon, text, confidence in rows or []:
                pieces.append((text, {"kind": "image_polygon", "unit": "pixel", "page": 1,
                    "polygon": [[float(x),float(y)] for x,y in polygon], "ocr_confidence": float(confidence)}))
        if not pieces: raise KBError("NO_READABLE_CONTENT")
        # Bounded subdocuments preserve every character; never silently truncate.
        chunks = []
        for text, locator in pieces:
            for offset in range(0, len(text), 512):
                chunks.append((text[offset:offset+512], {**locator, "char_offset": offset}))
        if len(chunks) > 2000: raise KBError("DOCUMENT_TOO_LARGE")
        raw_hash = self.store.object(raw)
        body_hash = digest(chunks)
        resource_id = digest(resource["url"])[:20]
        assets = []
        for start in range(0, len(chunks), 6):
            asset_id = "asset-" + digest({"resource": resource_id, "body": body_hash, "part": start//6})[:20]
            segments = [{"asset_id": asset_id, "segment_id": "segment-"+digest({"asset": asset_id, "locator": loc})[:20],
                         "content": text, "locator": loc} for text, loc in chunks[start:start+6]]
            assets.append({"asset_id": asset_id, "source_id": source["id"], "source_url": resource["url"],
                "title": source["title"], "modality": "image" if fmt in {"png", "jpg", "jpeg"} else "table" if fmt == "csv" else "text",
                "data_class": source["data_class"], "sha256": raw_hash, "body_sha256": body_hash,
                "format": fmt, "part": start//6, "segments": segments, "parsed": True, "review_status": "machine_candidate"})
        return body_hash, assets, raw_hash

    def fetch(self, source, previous=None, force_full=False):
        check_source(source, self.synthetic)
        resources = [{"url": source["url"], "format": source["format"], "encoding": source.get("encoding", "utf-8-sig")}]+source.get("attachment_urls", [])
        if len(resources) > 10: raise KBError("TOO_MANY_ATTACHMENTS")
        result = {"resources": {}, "assets": []}
        client = self.client or httpx.Client(trust_env=False, timeout=20, follow_redirects=False)
        try:
            for resource in resources:
                self.allowed(source, resource["url"])
                if resource["format"] not in FORMATS: raise KBError("UNSUPPORTED_FORMAT")
                old = (previous or {}).get("resources", {}).get(resource["url"], {})
                headers = {"User-Agent": "ICT-GovKnowledge/1.0 (registered public sources only)"}
                if not force_full:
                    if old.get("etag"): headers["If-None-Match"] = old["etag"]
                    if old.get("last_modified"): headers["If-Modified-Since"] = old["last_modified"]
                with client.stream("GET", resource["url"], headers=headers) as response:
                    if response.status_code == 304:
                        if force_full or not old: raise KBError("INVALID_NOT_MODIFIED")
                        entry = old
                    else:
                        if 300 <= response.status_code < 400: raise KBError("REDIRECT_REQUIRES_REGISTRATION")
                        if response.status_code != 200: raise KBError("SOURCE_HTTP_"+str(response.status_code))
                        data = bytearray()
                        for part in response.iter_bytes():
                            data.extend(part)
                            if len(data) > self.max_bytes: raise KBError("DOWNLOAD_TOO_LARGE")
                        body, assets, raw_hash = self.parse(source, resource, bytes(data), response.headers.get("content-type", ""))
                        entry = {"body_sha256": body, "raw_sha256": raw_hash, "assets": assets,
                            "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified")}
                        if old.get("body_sha256") == body:
                            entry["assets"] = old["assets"]  # Cosmetic raw changes do not invalidate old citations.
                    result["resources"][resource["url"]] = entry
                    result["assets"].extend(entry["assets"])
            result["body_sha256"] = digest({url: r["body_sha256"] for url, r in result["resources"].items()})
            return result
        except httpx.HTTPError:
            raise KBError("SOURCE_CONNECTION_FAILED") from None
        finally:
            if self.client is None: client.close()

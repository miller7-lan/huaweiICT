"""Text-layer PDF parser, run as a short-lived subprocess by the ingestion worker."""

import json
import re
import sys

from pypdf import PdfReader


def extract(path):
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
        raise ValueError("Encrypted or oversized PDF")
    chunks = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if len(text.strip()) < 8 or len(text) > 16000 or re.search(r"\d{17}[\dXx]", text):
            raise ValueError("Missing text layer, oversized page or possible personal identifier")
        chunks.append({"locator": f"PDF第{index+1}页", "category": "procedure", "text": text.strip()})
    return chunks


if __name__ == "__main__":
    print(json.dumps(extract(sys.argv[1]), ensure_ascii=True))

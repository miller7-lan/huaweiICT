"""Prepare a small actual-source preview, classified for later collection."""

import hashlib
import json
import re
import secrets
from html.parser import HTMLParser

from gov_mcp.m2 import M2Store
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def plain(data):
    parser = PlainText()
    parser.feed(data.decode("utf-8"))
    return re.sub(r"[ \t\r\u3000]+", " ", "".join(parser.parts))


def main():
    target = RUNTIME / "m2"
    ensure_private_directory(target)
    ensure_private_directory(target / "mcp")
    ensure_private_directory(target / "sources")
    source_manifest = json.loads((ROOT / "data/discovery/2026-10-13/source_manifest.json").read_text(encoding="utf-8"))
    manifest = {s["source_id"]: s for s in source_manifest["records"]}
    archive = RUNTIME / "data-discovery/2026-10-13"
    texts, sources = {}, []
    for sid in ("ID-LAW-01", "ID-GUIDE-01"):
        record = manifest[sid]
        data = (archive / (sid + ".html")).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != record["private_archive"]["sha256"]:
            raise ValueError("Actual official archive fingerprint differs from registered source")
        texts[sid] = plain(data)
        (target / "sources" / (sid + ".html")).write_bytes(data)
        sources.append({"source_id": sid, "title": record["title"], "url": record["url"],
            "region": record["region"], "document_type": record["document_type"], "sha256": sha,
            "review_status": "pending_business_review",
            "tags": {"matter": "id_card_renewal", "region": record["region"], "source_type": record["document_type"],
                     "applicability": "adult_local_renewal_proposed", "published_at": record["published_at"],
                     "effective_from": record["effective_from"], "usage": "private_engineering_preview"}})
    chunks = []
    law = texts["ID-LAW-01"]
    for start, end, category in (("第十条", "第十一条", "household"),
                                 ("第十一条", "第十二条", "identity"),
                                 ("第十二条", "第三章", "procedure"),
                                 ("第二条", "第三条", "eligibility")):
        matches = re.findall(re.escape(start) + r"\s*(.*?)" + re.escape(end), law, re.S)
        if len(matches) != 1 or len(matches[0].strip()) < 15:
            raise ValueError("Law article cannot be located unambiguously")
        text = start + " " + re.sub(r"\s+", " ", matches[0]).strip()
        chunks.append({"chunk_id": "ID-LAW-01-" + start, "source_id": "ID-LAW-01", "locator": start,
                       "category": category, "region": "national", "text": text})
    guide = texts["ID-GUIDE-01"]
    # Preserve the actual numbered statements as preview evidence, not a
    # synthesized mandatory-material list or a human-reviewed standard answer.
    for index, category in ((1, "household"), (2, "identity"), (3, "eligibility"), (4, "procedure")):
        ending = r"(?=\s*" + str(index+1) + r"、)" if index < 4 else r"(?=主办单位)"
        match = re.search(str(index) + r"、(.*?)" + ending, guide, re.S)
        if not match:
            raise ValueError("Guide material statement cannot be located")
        text = str(index) + "、" + re.sub(r"\s+", " ", match.group(1)).strip()
        chunks.append({"chunk_id": f"ID-GUIDE-01-material-{index}", "source_id": "ID-GUIDE-01",
            "locator": f"材料列表第{index}项（适用分支待复核）", "category": category,
            "region": "郑州市二七区", "text": text})
    (target / "mcp/phase.json").write_text(json.dumps({"phase": "M2", "mode": "engineering_demo", "schema_version": 1}), encoding="utf-8")
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    roles = {state["tenant_id"]: {state["accounts"][r]["user_id"]: {"role": r} for r in ("citizen", "staff")}}
    roles_path = target / "mcp/role-map.json"
    if not roles_path.exists():
        roles_path.write_text(json.dumps(roles), encoding="utf-8")
    token_path = target / "mcp-token.txt"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48), encoding="ascii")
    (target / "mcp.env").write_text("GOVM2_MCP_TOKEN=" + token_path.read_text(encoding="ascii").strip() + "\n", encoding="ascii")
    account_path = target / "accounts.json"
    if not account_path.exists():
        original = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        account_path.write_text(json.dumps({r: original[r] for r in ("citizen", "staff", "developer")}), encoding="utf-8")
    store = M2Store(target / "mcp")
    version = store.seed_preview(sources, chunks)
    public = {"mode": "engineering_demo", "knowledge_version": version, "counts": store.counts(),
        "source_sha256": {s["source_id"]: s["sha256"] for s in sources},
        "categories": sorted({c["category"] for c in chunks}), "classification_dimensions": ["source", "region", "matter", "material_category", "applicability", "knowledge_version"],
        "business_review_completed": False, "source_collection_phase": "small_preview_first_then_expand",
        "scope": "Two existing actual official archives; only M2 storage populated. All candidate rules pending business review."}
    (ROOT / "evidence/m2").mkdir(parents=True, exist_ok=True)
    (ROOT / "evidence/m2/source-preview.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    print("M2 classified preview prepared:", store.counts(), version)


if __name__ == "__main__":
    main()

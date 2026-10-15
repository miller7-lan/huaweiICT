"""Register existing leads and copy two fingerprint-verified archives as unreviewed candidates."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from gov_mcp.knowledge import KnowledgeStore, encode, fingerprint
from private_runtime import ensure_private_directory


ROOT = Path(__file__).resolve().parents[1]
REGION = "郑州市二七区"
MATTER = "demo-id-renewal"


def main():
    target = ROOT / ".runtime/knowledge-update"
    ensure_private_directory(target)
    store = KnowledgeStore.initialize(target)
    scope = json.loads((ROOT / "config/knowledge-scope.json").read_text(encoding="utf-8"))
    (target / "scope-regions.json").write_text(encode({scope["region"]: scope["applicable_regions"]}), encoding="utf-8")
    manifest = json.loads((ROOT / "data/discovery/2026-10-13/source_manifest.json").read_text(encoding="utf-8"))
    sources = []
    for record in manifest["records"]:
        url = urlsplit(record["url"])
        matter = ("demo-residence-permit" if record["source_id"].startswith("RES-") else
                  "out-of-scope-temporary-id" if record["source_id"] == "ID-BOUNDARY-01" else
                  "governance-only" if record["intended_role"] == "governance_reference" else
                  "excluded" if record["intended_role"] == "excluded_from_corpus" else MATTER)
        metadata = {"title": record["title"], "region": record["region"], "matter": matter,
                    "document_type": record["document_type"], "published_at": record.get("published_at"),
                    "effective_from": record.get("effective_from"), "effective_to": None,
                    "usage_basis": None, "scope_confirmed": True,
                    "business_review": "pending", "collection_role": record["intended_role"]}
        config = {"id": record["source_id"], "label": record["title"], "enabled": False,
                  "origin": f"{url.scheme}://{url.netloc}", "allowed_paths": [url.path], "seeds": [record["url"]],
                  "interval_seconds": 86400, "delay_seconds": 5, "metadata": metadata,
                  "content_selector": {"id": "content-3394639"} if record["source_id"] == "ID-GUIDE-01" else None,
                  "discover_links": False, "page_kind": "document",
                  "access_review": {"approved": False, "reason": "Current access/usage review pending; historical access is not current authorization"}}
        with store.db() as existing_db:
            existing = existing_db.execute("SELECT config FROM sites WHERE id=?", (config["id"],)).fetchone()
        existing_config = json.loads(existing[0]) if existing else None
        if existing_config and (existing_config["enabled"] or existing_config.get("access_review", {}).get("approved")):
            config = existing_config
        else:
            store.register(config)
        sources.append(config)
    with store.db() as existing_db:
        extra_sources = [json.loads(row[0]) for row in existing_db.execute("SELECT config FROM sites")
                         if json.loads(row[0])["id"] not in {source["id"] for source in sources}]
    (target / "sources.json").write_text(json.dumps({"sources": sources + extra_sources}, ensure_ascii=False, indent=2), encoding="utf-8")
    imported = []
    preview = ROOT / ".runtime/m2/mcp/m2.sqlite3"
    if preview.exists():
        with sqlite3.connect(preview.resolve().as_uri()+"?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            for sid in ("ID-LAW-01", "ID-GUIDE-01"):
                record = next(r for r in manifest["records"] if r["source_id"] == sid)
                raw = (ROOT / ".runtime/data-discovery/2026-10-13" / (sid+".html")).read_bytes()
                if fingerprint(raw) != record["private_archive"]["sha256"]:
                    raise ValueError("Historical original fingerprint mismatch")
                source = db.execute("SELECT sha256 FROM sources WHERE source_id=?", (sid,)).fetchone()
                if not source or source[0] != fingerprint(raw):
                    raise ValueError("Preview source does not match original")
                version = db.execute("SELECT version_id FROM chunks WHERE source_id=? ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
                if not version:
                    raise ValueError("Preview contains no located chunks")
                chunks = [dict(r) for r in db.execute("SELECT locator,category,text FROM chunks WHERE source_id=? AND version_id=? ORDER BY rowid", (sid, version[0]))]
                metadata = next(c["metadata"] for c in sources if c["id"] == sid)
                # Preparation is initial seeding, never a request to revert newer observed content.
                with store.db() as target_db:
                    current = target_db.execute("SELECT r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (sid, record["url"])).fetchone()
                imported.append(current[0] if current else store.ingest(sid, record["url"], raw, metadata, chunks))
    record = {"prepared_at": datetime.now(timezone.utc).isoformat(),
              "scope": {"region": REGION, "matter": MATTER, "confirmation": "User confirmed existing candidate scope on 2026-10-15"},
              "imported_revisions": imported, "counts": store.status()["counts"],
              "network_requests": 0, "official_publications": 0, "business_review_completed": False,
              "note": "Existing two archives copied as pending candidates; no new official documents claimed."}
    (target / "preparation.json").write_text(encode(record), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

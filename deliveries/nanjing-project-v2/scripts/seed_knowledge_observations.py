"""Seed page signals from already archived originals, making no new network/freshness claim."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import observe_html


ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    records = []
    for source_id in ("MPS-NORM-01", "MPS-USE-01"):
        with store.db() as db:
            config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (source_id,)).fetchone()[0])
            rows = [dict(r) for r in db.execute("SELECT r.*,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)", (source_id,))]
        config["metadata_selectors"] = {"document_fields": {"class": "doc-info"}}
        # Register the changed extraction policy; subsequent checks still pass through robots/rate limits.
        store.register(config)
        for row in rows:
            raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
            if fingerprint(raw) != row["sha256"]:
                raise ValueError("Stored original is corrupted")
            metadata = json.loads(row["metadata"])
            if row["state"] != "pending_review":
                raise ValueError("Do not seed observations into already decided business records")
            metadata["source_observation"] = observe_html(raw, config["metadata_selectors"])
            with store.db() as db:
                _, chunks = store._content(db, row["id"])
            target = store.ingest(source_id, row["url"], raw, metadata, chunks)
            records.append({"source_id": source_id, "previous_revision": row["id"], "revision": target,
                "sha256": row["sha256"], "observation": metadata["source_observation"]})
    with store.db() as db:
        configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "real_network_requests_by_this_script": 0,
        "basis": "Previously acquired hash-verified originals, not a new website or current-effectivity check",
        "business_review_completed": False, "official_knowledge_published": 0, "records": records,
        "counts": store.status()["counts"]}
    (ROOT / "evidence/knowledge-update/page-observation-baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(records), "counts": report["counts"], "network_requests_by_script": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

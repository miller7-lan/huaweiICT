"""Export verified latest-revision summaries; policy review remains separate."""

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_impact import duplicate_candidates, revision_impact


ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    with store.db() as db:
        rows = [dict(row) for row in db.execute("SELECT d.site_id,r.id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY d.site_id")]
    records = [dict(revision_impact(store, row["id"]), source_id=row["site_id"]) for row in rows]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "records": records,
              "possible_duplicates": duplicate_candidates(store), "business_review_completed": False,
              "scope": "Actual archived candidate differences and potential mirror signals; no policy effectivity decision or automatic consolidation"}
    (ROOT / "evidence/knowledge-update/revision-impacts.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(records), "metadata_deltas": sum("metadata_change" in record["change_kinds"] for record in records),
                      "duplicate_groups": len(report["possible_duplicates"]["groups"]), "publications_changed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

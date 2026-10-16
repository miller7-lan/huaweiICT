"""Store derived review suggestions without changing revisions, approvals or publications."""

import json
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore
from nexent_api import ROOT, RUNTIME


def main():
    store = KnowledgeStore(RUNTIME / "knowledge-update")
    before = store.status()["counts"]
    with store.db() as db:
        revisions = [dict(row) for row in db.execute("SELECT r.id,d.site_id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)")]
    records = []
    for row in revisions:
        proposal = store.propose_classification(row["id"])
        records.append({"revision": row["id"], "source_id": row["site_id"], "taxonomy_version": proposal["taxonomy_version"],
            "topic_codes": proposal["topic_codes"], "mixed_branch_mentions": proposal["mixed_branch_mentions"],
            "sections": len(proposal["sections"]), "status": proposal["status"],
            "literal_structure_markers": sum(len(s["source_structure"]["markers"]) for s in proposal["sections"]),
            "structure_limit_sections": sum(s["source_structure"]["truncated"] for s in proposal["sections"]),
            "structure_cautions": sorted({code for s in proposal["sections"] for code in s["source_structure"]["cautions"]})})
    after = store.status()["counts"]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "records": records,
        "before_counts": before, "after_counts": after, "business_review_completed": False,
        "unchanged_revision_and_publication_counts": before == after,
        "note": "Topic/branch labels and literal condition/stage/purpose/time markers are unapproved review proposals; required/conditional/alternative material relationships and current effectivity remain unknown. No source bodies are exported in this summary."}
    (ROOT / "evidence/knowledge-update/classification-proposals.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"proposed_documents": len(records), "revisions_unchanged": before["revisions"] == after["revisions"], "official_publications": after["heads"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Read-only source-scope annotations for reviewers, separate from approval and test oracles."""
from copy import deepcopy

from gov_mcp.knowledge import fingerprint


def bind_scope_notes(store, artifact, scope_sha256):
    result = {"kind": "review_only_scope_notes_not_approval_or_answer_evidence", "status": "missing",
              "notes": [], "withheld": [], "business_approval": False,
              "limits": "Binding checks establish source identity, not the truth of AI analysis, legal currentness, reviewer approval or reference independence."}
    if artifact is None:
        return result
    if (not isinstance(artifact, dict) or type(artifact.get("schema")) is not int or artifact.get("schema") != 1
            or artifact.get("kind") != "source_scope_notes_not_approval_or_oracle"
            or fingerprint({k: v for k, v in artifact.items() if k != "content_sha256"}) != artifact.get("content_sha256")
            or not isinstance(artifact.get("notes"), list) or not 1 <= len(artifact["notes"]) <= 20):
        result.update(status="invalid", withheld=[{"reason": "artifact_invalid"}])
        return result
    identifiers = [note.get("id") for note in artifact["notes"] if isinstance(note, dict)]
    if len(identifiers) != len(artifact["notes"]) or any(not isinstance(key, str) or not key for key in identifiers) or len(set(identifiers)) != len(identifiers):
        result.update(status="invalid", withheld=[{"reason": "note_identity_invalid"}])
        return result
    for note in artifact["notes"]:
        try:
            if note["scope_sha256"] != scope_sha256:
                raise ValueError("scope_changed")
            if (note["reviewer_kind"] != "ai" or note["human_review_completed"] is not False
                    or note["business_approval"] is not False or note["reference_oracle"] is not False
                    or note["current_eligibility_certified"] is not False):
                raise ValueError("annotation_cannot_carry_approval")
            if (not isinstance(note["sources"], dict) or not 1 <= len(note["sources"]) <= 8
                    or not isinstance(note["anchors"], list) or not 1 <= len(note["anchors"]) <= 100
                    or not isinstance(note["facts"], list) or not 1 <= len(note["facts"]) <= 20):
                raise ValueError("note_incomplete")
            sources = {}
            with store.db() as db:
                for key, binding in note["sources"].items():
                    row = db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.id=?", (binding["revision"],)).fetchone()
                    if row is None:
                        raise ValueError("source_missing")
                    latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
                    if latest != row["id"]:
                        raise ValueError("source_superseded")
                    actual = {"source_id": row["site_id"], "url": row["url"], "revision": row["id"],
                              "original_sha256": row["sha256"], "body_sha256": row["body_sha256"],
                              "metadata_sha256": fingerprint(row["metadata"].encode()), "state": row["state"]}
                    if binding != actual:
                        raise ValueError("source_binding_changed")
                    store._verify(db, row["id"], require_review=row["state"] == "reviewed")
                    _, chunks = store._content(db, row["id"])
                    sources[key] = chunks
                anchors = {}
                for anchor in note["anchors"]:
                    if anchor["id"] in anchors or type(anchor["ordinal"]) is not int or anchor["ordinal"] < 0:
                        raise ValueError("anchor_identity_invalid")
                    chunk = sources[anchor["source_key"]][anchor["ordinal"]]
                    if chunk["locator"] != anchor["locator"] or fingerprint(chunk["text"].encode()) != anchor["text_sha256"]:
                        raise ValueError("literal_anchor_changed")
                    anchors[anchor["id"]] = anchor
            if any(not isinstance(fact["finding"], str) or not fact["finding"]
                   or not isinstance(fact["limits"], str) or not fact["limits"]
                   or not isinstance(fact["anchor_ids"], list) or not fact["anchor_ids"]
                   or any(key not in anchors for key in fact["anchor_ids"]) for fact in note["facts"]):
                raise ValueError("fact_reference_invalid")
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            reason = str(error) if isinstance(error, ValueError) and str(error) in {
                "scope_changed", "annotation_cannot_carry_approval", "note_incomplete", "source_missing",
                "source_superseded", "source_binding_changed", "anchor_identity_invalid", "literal_anchor_changed",
                "fact_reference_invalid"} else "source_or_annotation_invalid"
            result["withheld"].append({"id": note["id"], "reason": reason})
            continue
        result["notes"].append(deepcopy(note))
    result["status"] = "bound" if not result["withheld"] else "partially_bound" if result["notes"] else "stale"
    return result

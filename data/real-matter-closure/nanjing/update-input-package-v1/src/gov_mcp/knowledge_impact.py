"""Read-only, fingerprint-checked revision deltas and possible mirror groups.

These are review aids. Neither text similarity nor matching document numbers is
an applicability decision, a revocation decision or authority to merge records.
"""

import json
import re
import unicodedata
from collections import defaultdict

from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_classify import suggest


VERSION = "revision-impact-v1"
MAX_DETAILS = 100


def compact(text):
    return re.sub(r"\s+", "", text)


def field_deltas(before, after, path="", depth=0):
    """Keep only bounded leaf paths; values stay in the private review packet."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict) and depth < 8:
        result = []
        for key in sorted(set(before) | set(after)):
            child = path+"."+key if path else key
            if key not in before or key not in after:
                result.append({"field": child, "before_present": key in before, "after_present": key in after,
                               "before": before.get(key), "after": after.get(key)})
            else:
                result.extend(field_deltas(before[key], after[key], child, depth+1))
        return result
    return [{"field": path, "before": before, "after": after}]


def localized(chunks):
    values = defaultdict(list)
    for chunk in chunks:
        values[chunk["locator"]].append({"category": chunk["category"], "text": chunk["text"]})
    return dict(values)


def revision_impact(store, revision, base=None, include_excerpts=False):
    with store.db() as db:
        db.execute("BEGIN")
        store._verify(db, revision, require_review=False)
        current, current_chunks = store._content(db, revision)
        position = db.execute("SELECT rowid FROM revisions WHERE id=?", (revision,)).fetchone()[0]
        previous = db.execute("SELECT id FROM revisions WHERE document_id=? AND rowid<? ORDER BY rowid DESC LIMIT 1", (current["document_id"], position)).fetchone()
        if base is None:
            base = previous[0] if previous else None
        old, old_chunks = None, []
        if base:
            store._verify(db, base, require_review=False)
            old, old_chunks = store._content(db, base)
            old_position = db.execute("SELECT rowid FROM revisions WHERE id=?", (base,)).fetchone()[0]
            if old["document_id"] != current["document_id"] or old_position >= position:
                raise ValueError("Impact base must be an earlier revision of the same document")
    metadata = json.loads(current["metadata"])
    old_metadata = json.loads(old["metadata"]) if old else {}
    # The chronology marker is an ingest mechanism rather than a policy field.
    metadata.pop("_observed_after_revision", None)
    old_metadata.pop("_observed_after_revision", None)
    fields = field_deltas(old_metadata, metadata) if old else []
    before, after = localized(old_chunks), localized(current_chunks)
    added, removed = sorted(after.keys()-before.keys()), sorted(before.keys()-after.keys())
    text_changed, category_changed, formatting_only = [], [], []
    for locator in sorted(before.keys() & after.keys()):
        a, b = before[locator], after[locator]
        if [item["category"] for item in a] != [item["category"] for item in b]:
            category_changed.append(locator)
        if [compact(item["text"]) for item in a] != [compact(item["text"]) for item in b]:
            text_changed.append(locator)
        elif [item["text"] for item in a] != [item["text"] for item in b]:
            formatting_only.append(locator)
    changed_locators = sorted(set(added+removed+text_changed+category_changed))
    affected_chunks = [chunk for chunk in old_chunks+current_chunks if chunk["locator"] in changed_locators]
    suggestions = suggest(affected_chunks, metadata)
    details = []
    if include_excerpts:
        for locator in changed_locators[:MAX_DETAILS]:
            details.append({"locator": locator,
                            "before": before.get(locator, []), "after": after.get(locator, [])})
    kinds = []
    if old is None:
        kinds.append("initial_candidate")
    else:
        if added or removed or text_changed:
            kinds.append("content_or_locator_change")
        if category_changed:
            kinds.append("classification_change")
        if fields:
            kinds.append("metadata_change")
        if formatting_only:
            kinds.append("formatting_change")
        if current["sha256"] != old["sha256"] and not (added or removed or text_changed or category_changed or fields or formatting_only):
            kinds.append("raw_representation_only")
        if not kinds:
            kinds.append("no_semantic_delta_detected")
    report = {"analysis_version": VERSION, "revision": revision, "base_revision": base,
              "status": "machine_analysis_not_review", "change_kinds": kinds,
              "original_sha256": current["sha256"], "base_original_sha256": old["sha256"] if old else None,
              "chunk_counts": {"before": len(old_chunks), "after": len(current_chunks)},
              "added_locators": added, "removed_locators": removed, "changed_text_locators": text_changed,
              "changed_category_locators": category_changed, "formatting_only_locators": formatting_only,
              "changed_metadata_fields": fields if include_excerpts else [field["field"] for field in fields],
              "affected_topic_codes": suggestions["topic_codes"], "mixed_branch_mentions": suggestions["mixed_branch_mentions"],
              "details": details, "details_truncated": include_excerpts and len(changed_locators) > MAX_DETAILS,
              "warning": "Detected differences require source/scope review; absence of a detected difference is not proof of current validity"}
    report["analysis_sha256"] = fingerprint(report)
    return report


def duplicate_candidates(store):
    """Group current records without merging or changing revisions/publications."""
    groups = defaultdict(list)
    shared_clauses = defaultdict(dict)
    with store.db() as db:
        db.execute("BEGIN")
        records = db.execute("SELECT r.*,d.site_id,d.url FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id) ORDER BY r.id").fetchall()
        for row in records:
            store._verify(db, row["id"], require_review=False)
            _, chunks = store._content(db, row["id"])
            meta = json.loads(row["metadata"])
            member = {"revision": row["id"], "source_id": row["site_id"], "url": row["url"],
                      "state": row["state"], "original_sha256": row["sha256"]}
            groups[("same_original_bytes", row["sha256"])].append(member)
            text = compact("\n".join(chunk["text"] for chunk in chunks))
            if len(text) >= 80:
                groups[("same_normalized_selected_text", fingerprint(text.encode()))].append(member)
            for chunk in chunks:
                clause = compact(chunk["text"])
                if len(clause) < 80:
                    continue
                key = fingerprint(clause.encode())
                matching = shared_clauses[key].setdefault(row["id"], dict(member, locators=[]))
                if chunk["locator"] not in matching["locators"]:
                    matching["locators"].append(chunk["locator"])
            number = meta.get("document_number")
            if isinstance(number, str) and number.strip():
                number = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", number)).lower()
                if len(number) >= 5:
                    groups[("same_reported_document_number", fingerprint(number.encode()))].append(member)
    matches = [{"signal": signal, "group_fingerprint": key, "members": members,
                "interpretation": "Possible mirror, duplicate or different version; verify identity, dates and text before consolidation"}
               for (signal, key), members in sorted(groups.items()) if len(members) > 1]
    matches.extend({"signal": "shared_normalized_clause_text", "group_fingerprint": key, "members": list(members.values()),
                    "interpretation": "Some selected clauses match; this does not establish that whole documents or effective versions are identical"}
                   for key, members in sorted(shared_clauses.items()) if len(members) > 1)
    result = {"analysis_version": VERSION, "status": "machine_candidate_groups_no_merge", "groups": matches,
              "current_document_records": len(records), "distinct_original_hashes": len({row["sha256"] for row in records}),
              "warning": "Records with the same reported number can be distinct versions; similarity never grants publication or merges scope"}
    result["analysis_sha256"] = fingerprint(result)
    return result

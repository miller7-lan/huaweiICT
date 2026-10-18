"""Read-only public candidate retrieval for the fixed Beijing/Nanjing experiment.

This does not review candidates, publish a version, or infer business eligibility.
"""
import hashlib
import json
import re

from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge_retrieval import query_grams, score_literal


TARGETS = {
    "BJ-ID-RENEWAL-2025": ("北京市", "resident-id-renewal"),
    "NJ-ID-RENEWAL-CYCLE-2026": ("南京市", "resident-id-renewal"),
}
URLS = {
    "BJ-ID-RENEWAL-2025": "https://www.beijing.gov.cn/fuwu/bmfw/bmzt/sfz/blzn/201806/t20180622_1857526.html",
    "NJ-ID-RENEWAL-CYCLE-2026": "https://www.nanjing.gov.cn/hdjl/hygq/202601/t20260105_5757179.html",
}


def verified_chunks(raw, spec, document):
    """Fail closed on any selected clause or paragraph layout drift."""
    source = spec["source_id"]
    if (source not in TARGETS or (spec["region"], spec["matter"]) != TARGETS[source]
            or spec["url"] != URLS[source]):
        raise ValueError("Source scope outside fixed experiment")
    meta = document["metadata"]
    if (document["source_id"] != source or document["url"] != spec["url"]
            or (meta["region"], meta["matter"]) != TARGETS[source]
            or document["original_sha256"] != spec["original_sha256"]):
        raise ValueError("Catalog and batch identity differ")
    # Shared ordinals count short headings too. The general collector's default
    # 8-character filter would silently renumber this Beijing guide.
    chunks, _ = parse_html(raw, spec["selector"], min_text_length=1)
    if len(chunks) != spec["parsed_paragraph_count"]:
        raise ValueError("Paragraph layout changed")
    ordinals = spec["selected_ordinals"]
    expected = document["selected_clauses"]
    if len(set(ordinals)) != len(ordinals) or len(ordinals) != len(expected):
        raise ValueError("Ambiguous clause selection")
    selected = []
    for ordinal, clause in zip(ordinals, expected):
        if type(ordinal) is not int or not 1 <= ordinal <= len(chunks):
            raise ValueError("Invalid paragraph ordinal")
        chunk = chunks[ordinal - 1]
        if (clause["locator"] != chunk["locator"] or clause["category"] != chunk["category"]
                or clause["characters"] != len(chunk["text"])
                or clause["text_sha256"] != hashlib.sha256(chunk["text"].encode()).hexdigest()):
            raise ValueError("Exact quote differs from shared catalog")
        selected.append(chunk)
    return selected


def retrieve(store, bindings, query, region, matter):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ValueError("Bounded question required")
    if re.search(r"\d{17}[\dXx]", query):
        raise ValueError("Applicant identifier refused")
    result = {"mode": "public_candidate_preview", "formally_published": False,
              "business_conclusion_status": "PAUSED_UNREVIEWED", "citations": [],
              "region": region, "matter": matter, "source_scope_limits": [], "source_context": []}
    if (region, matter) not in TARGETS.values():
        return dict(result, status="OUT_OF_SCOPE")
    ranked = []
    grams = query_grams(query)
    with store.db() as db:
        for binding in bindings:
            spec, document = binding["spec"], binding["document"]
            if spec["source_id"] not in TARGETS:
                raise ValueError("Unexpected source")
            if TARGETS[spec["source_id"]] != (region, matter):
                continue
            site = db.execute("SELECT config,paused_reason FROM sites WHERE id=?", (spec["source_id"],)).fetchone()
            if not site or site["paused_reason"] or not json.loads(site["config"])["enabled"]:
                raise ValueError("Source disabled or paused")
            row, _ = store._verify(db, binding["revision_id"], require_review=False)
            meta = json.loads(row["metadata"])
            doc = db.execute("SELECT site_id,url FROM documents WHERE id=?", (row["document_id"],)).fetchone()
            if (doc["site_id"] != spec["source_id"] or doc["url"] != spec["url"]
                    or (meta["region"], meta["matter"]) != (region, matter)
                    or row["state"] != "pending_review"):
                raise ValueError("Candidate identity or state changed")
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1",
                                (row["document_id"],)).fetchone()[0]
            if latest != row["id"]:
                raise ValueError("Snapshot superseded; reacquisition required")
            raw = (store.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
            chunks = verified_chunks(raw, spec, document)
            _, persisted = store._content(db, row["id"])
            if chunks != persisted:
                raise ValueError("Stored quotes differ from original")
            before = len(ranked)
            for chunk in chunks:
                score = score_literal(grams, chunk["text"])
                if score:
                    ranked.append((score, {"source_id": spec["source_id"], "url": spec["url"],
                        "revision_id": row["id"], "original_sha256": row["sha256"],
                        "locator": chunk["locator"], "quote": chunk["text"],
                        "text_sha256": hashlib.sha256(chunk["text"].encode()).hexdigest(),
                        "published_at": meta.get("published_at"), "effective_from": None,
                        "effective_to": None, "current_applicability": "unknown"}))
            if len(ranked) > before:
                result["source_scope_limits"].append({"source_id": spec["source_id"], "limits": spec["scope_limits"]})
                # Ranking must never drop a qualifying continuation (e.g. the
                # Beijing damaged-card online conditions in paragraph 25).
                result["source_context"].append({"source_id": spec["source_id"], "url": spec["url"],
                    "revision_id": row["id"], "original_sha256": row["sha256"],
                    "selected_clauses": chunks, "unselected_body_not_included": True})
    result["citations"] = [item for _, item in sorted(ranked, key=lambda x: (-x[0], x[1]["locator"]))[:5]]
    return dict(result, status="SOURCE_EXCERPTS" if ranked else "NO_MATCH")

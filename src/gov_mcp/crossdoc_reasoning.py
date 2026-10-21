"""Fixed, attributed cross-document reasoning over historical public archives.

The engineering scope mapping below is not approval of current business rules.
No network, model, publication or calendar conversion occurs in this module.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge.store import digest


SOURCE_IDENTITY = {
    "cycle": {
        "source_id": "NJ-ID-RENEWAL-CYCLE-2026",
        "url": "https://www.nanjing.gov.cn/hdjl/hygq/202601/t20260105_5757179.html",
        "region": "南京市", "matter": "resident-id-renewal",
        "raw_sha256": "feb2b5afd628f4bef72ad36b24969a4c972c04adabae5afa4872f94d28c10920",
    },
    "old_card": {
        "source_id": "NJ-ID-OLD-CARD-2025",
        "url": "https://www.nanjing.gov.cn/zgnjsjb/hdjl/rdwt/202512/t20251209_5709611.html",
        "region": "南京市", "matter": "resident-id-renewal-old-card-use",
        "raw_sha256": "9b668bfdb6c81d88692e1e3f684e327181ee0096b57b5d40b2252507d7a6d88c",
    },
}
SCOPE_MAPPING = {
    "region": "南京市", "from_matter": "resident-id-renewal-old-card-use",
    "to_matter": "resident-id-renewal",
    "approved_for": "engineering_fixed_preview_only", "formal_approval": False,
}
SCENARIO_FACTS = {
    "region": "南京市", "matter": "resident-id-renewal",
    "household": "outside_province", "remaining_validity_working_days": 22,
    "normalized_same_start_and_unit": True, "lost_or_reported": False,
    "exam_expedited": False,
}
ARCHIVE_MODE = "frozen_historical_archive_not_current_policy"
SOURCE_LAYOUT = {"cycle": ({"id": "con"}, 3), "old_card": ({"class": "TRS_UEDITOR"}, 2)}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))


def checked_segments(raw, spec, catalog_document):
    """Check the entire selected body, retaining short headings and exceptions."""
    if (_sha(raw) != spec["original_sha256"]
            or catalog_document["original_sha256"] != spec["original_sha256"]
            or len(raw) != catalog_document["original_bytes"]):
        raise ValueError("ARCHIVE_RAW_OR_CATALOG_CHANGED")
    chunks, _ = parse_html(raw, spec["selector"], min_text_length=1)
    ordinals, expected = spec["selected_ordinals"], catalog_document["selected_clauses"]
    if (len(chunks) != spec["parsed_paragraph_count"] or len(ordinals) != len(expected)
            or len(set(ordinals)) != len(ordinals)
            or ordinals != list(range(1, len(chunks) + 1))):
        raise ValueError("ALL_BODY_PARAGRAPHS_REQUIRED")
    segments = []
    for ordinal, clause in zip(ordinals, expected):
        chunk = chunks[ordinal - 1]
        if (clause["locator"] != chunk["locator"] or clause["category"] != chunk["category"]
                or clause["characters"] != len(chunk["text"])
                or clause["text_sha256"] != _sha(chunk["text"].encode())):
            raise ValueError("ARCHIVE_PARAGRAPH_CHANGED")
        segments.append({"locator": chunk["locator"], "quote": chunk["text"],
                         "text_sha256": clause["text_sha256"]})
    return segments


def load_bundle(root):
    """Reread raw bytes, selected quotes and local pause gates on every call.

    Historical snapshots never acquire a renewed freshness period. A source
    supersession/failed/paused flag blocks this engineering snapshot immediately.
    """
    root = Path(root).resolve()
    manifest_raw, gates_raw = (root / "manifest.json").read_bytes(), (root / "gates.json").read_bytes()
    manifest, gates = _json(manifest_raw), _json(gates_raw)
    if (manifest.get("format") != "crossdoc-source-bundle-v1"
            or manifest.get("archive_mode") != ARCHIVE_MODE
            or manifest.get("current_applicability") != "unknown"
            or manifest.get("formal_approval") is not False
            or manifest.get("formally_published") is not False
            or set(manifest.get("sources", {})) != set(SOURCE_IDENTITY)
            or gates.get("format") != "crossdoc-source-gates-v1"
            or gates.get("archive_mode") != ARCHIVE_MODE
            or gates.get("business_conclusions_paused") is not True
            or gates.get("scope_mapping") != SCOPE_MAPPING
            or set(gates.get("sources", {})) != set(SOURCE_IDENTITY)):
        raise ValueError("FIXED_ARCHIVE_SCOPE_AND_PAUSE_GATES_REQUIRED")
    documents, raw_bindings = {}, {}
    for key, identity in SOURCE_IDENTITY.items():
        gate, entry = gates["sources"][key], manifest["sources"][key]
        if (set(gate) != {"enabled", "paused_reason", "failed", "superseded"}
                or gate["enabled"] is not True or gate["paused_reason"] is not None
                or gate["failed"] is not False or gate["superseded"] is not False):
            raise ValueError("SOURCE_PAUSED_FAILED_DISABLED_OR_SUPERSEDED")
        if any(entry.get(field) != value for field, value in identity.items()):
            raise ValueError("SOURCE_IDENTITY_OR_SCOPE_CHANGED")
        spec, catalog_document = entry["spec"], entry["catalog_document"]
        meta = catalog_document["metadata"]
        selector, paragraph_count = SOURCE_LAYOUT[key]
        if (spec.get("selector") != selector or spec.get("parsed_paragraph_count") != paragraph_count
                or spec.get("selected_ordinals") != list(range(1, paragraph_count + 1))):
            raise ValueError("FIXED_FULL_BODY_EXTRACTION_REQUIRED")
        for field in ("source_id", "url"):
            if spec[field] != identity[field] or catalog_document[field] != identity[field]:
                raise ValueError("CATALOG_IDENTITY_CHANGED")
        if ((spec["region"], spec["matter"]) != (identity["region"], identity["matter"])
                or (meta["region"], meta["matter"]) != (identity["region"], identity["matter"])
                or spec["original_sha256"] != identity["raw_sha256"]
                or entry.get("current_applicability") != "unknown"
                or entry.get("effective_from") is not None or entry.get("effective_to") is not None
                or entry.get("formal_approval") is not False
                or entry.get("formally_published") is not False
                or entry.get("published_at") != meta.get("published_at")):
            raise ValueError("SOURCE_METADATA_CHANGED")
        expected_path = "sources/" + identity["source_id"] + ".html"
        if entry.get("raw_path") != expected_path:
            raise ValueError("FIXED_LOCAL_ARCHIVE_PATH_REQUIRED")
        path = (root / expected_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("ARCHIVE_PATH_OUTSIDE_ROOT")
        raw = path.read_bytes()
        segments = checked_segments(raw, spec, catalog_document)
        if entry.get("segments") != segments:
            raise ValueError("PERSISTED_QUOTES_CHANGED")
        documents[key] = {**identity, "segments": segments, "published_at": entry["published_at"],
                          "current_applicability": "unknown", "effective_from": None, "effective_to": None,
                          "formal_approval": False, "formally_published": False,
                          "archive_mode": ARCHIVE_MODE, "scope_mapping": deepcopy(SCOPE_MAPPING),
                          "revision": catalog_document["revision"]}
        raw_bindings[key] = _sha(raw)
    if ((root / "manifest.json").read_bytes() != manifest_raw or (root / "gates.json").read_bytes() != gates_raw
            or any(_sha((root / manifest["sources"][key]["raw_path"]).read_bytes()) != value
                   for key, value in raw_bindings.items())):
        raise ValueError("SOURCE_CHANGED_DURING_READ")
    return {"documents": documents, "binding": digest({"manifest": manifest, "gates": gates,
                                                        "originals": raw_bindings}),
            "manifest": manifest, "gates": gates}


def _citation(document, segment):
    return {key: document[key] for key in ("source_id", "url", "raw_sha256", "region", "matter")} | deepcopy(segment)


def solve(documents, facts):
    """Three-hop DAG: waiting period -> usable interval -> interval comparison.

    Input days are a fictional, already normalized interval from the same start.
    The result compares archive statements, never actual processing guarantees.
    """
    result = {"schema_version": 1, "status": "unknown", "conclusion": None, "reason": None,
              "facts": {}, "trace": [], "scope_mapping": deepcopy(SCOPE_MAPPING),
              "archive_mode": ARCHIVE_MODE, "current_applicability": "unknown",
              "business_conclusion_status": "PAUSED_UNREVIEWED", "formally_published": False,
              "formal_approval": False, "actual_issuance_date": None,
              "actual_gap_days": None}
    def stop(reason, status="unknown"):
        return dict(result, reason=reason, status=status)
    if not isinstance(facts, dict) or set(facts) != set(SCENARIO_FACTS):
        return stop("EXACT_FICTIONAL_FACT_FIELDS_REQUIRED")
    # Never return unknown or privacy-bearing input fields to the caller.
    if ((facts["region"], facts["matter"]) != ("南京市", "resident-id-renewal")
            or facts["household"] not in ("outside_province", "inside_province")):
        return stop("REGION_MATTER_OR_HOUSEHOLD_OUT_OF_SCOPE", "out_of_scope")
    if (type(facts["remaining_validity_working_days"]) is not int
            or not 0 < facts["remaining_validity_working_days"] <= 3660
            or facts["normalized_same_start_and_unit"] is not True
            or facts["lost_or_reported"] is not False or facts["exam_expedited"] is not False):
        return stop("UNNORMALIZED_MISSING_OR_SPECIAL_CASE_FACTS")
    result["facts"] = deepcopy(facts)
    if not isinstance(documents, dict) or set(documents) != set(SOURCE_IDENTITY):
        return stop("BOTH_DISTINCT_SOURCE_DOCUMENTS_REQUIRED")
    for key, identity in SOURCE_IDENTITY.items():
        doc = documents[key]
        if (not isinstance(doc, dict) or any(doc.get(f) != identity[f] for f in ("source_id", "url", "region", "matter"))
                or doc.get("current_applicability") != "unknown"
                or doc.get("formal_approval") is not False or doc.get("formally_published") is not False
                or doc.get("archive_mode") != ARCHIVE_MODE or doc.get("scope_mapping") != SCOPE_MAPPING
                or not isinstance(doc.get("raw_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", doc["raw_sha256"]) is None):
            return stop("SOURCE_SCOPE_OR_ENGINEERING_MAPPING_REQUIRED")
        segments = doc.get("segments")
        expected_count = 3 if key == "cycle" else 2
        if (not isinstance(segments, list) or len(segments) != expected_count
                or any(not isinstance(s, dict) or set(s) != {"locator", "quote", "text_sha256"}
                       or s["locator"] != f"正文段落{i}" or not isinstance(s["quote"], str)
                       or s["text_sha256"] != _sha(s["quote"].encode()) for i, s in enumerate(segments, 1))):
            return stop("COMPLETE_EXACT_SOURCE_SEGMENTS_REQUIRED")
    cycle, old_card = documents["cycle"], documents["old_card"]
    cycle_segments, old_segments = cycle["segments"], old_card["segments"]
    pattern = (r"答：省内户籍居民身份证换发周期为([1-9][0-9]*)个工作日；"
               r"省外户籍居民身份证需由户籍地公安机关审核签发，换发周期增加([1-9][0-9]*)个工作日。")
    parsed = re.fullmatch(pattern, cycle_segments[1]["quote"])
    if (cycle_segments[0]["quote"] != "问：身份证到期后换证，需要多长时间才能领取新证？"
            or cycle_segments[2]["quote"] != "对急需用证考生，实行制证信息快速受理、审签、制证、发放，最大限度缩短证件制发周期，确保考生顺利参加考试。"
            or parsed is None
            or old_segments[0]["quote"] != "问：申请换领居民身份证期间，旧身份证还能用吗？"
            or old_segments[1]["quote"] != "答：申请换领居民身份证期间，未超期的旧身份证可以继续使用。"):
        return stop("UNRECOGNIZED_OR_CONFLICTING_SOURCE_CLAUSES")
    base, increment = (int(value) for value in parsed.groups())
    waiting = base + (increment if facts["household"] == "outside_province" else 0)
    remaining = facts["remaining_validity_working_days"]
    result["trace"] = [
        {"id": "c1", "depends_on": [], "rule": "select_household_branch_and_add_increment",
         "inputs": {"household": facts["household"], "base_working_days": base,
                    "outside_increment_working_days": increment},
         "output": {"usual_waiting_working_days": waiting},
         "evidence": [_citation(cycle, s) for s in cycle_segments]},
        {"id": "u1", "depends_on": ["c1"], "rule": "intersect_waiting_interval_with_unexpired_old_card_interval",
         "inputs": {"waiting_interval_from": "c1.output.usual_waiting_working_days",
                    "remaining_validity_working_days": remaining, "normalized_same_start_and_unit": True},
         "output": {"old_card_interval_working_days": min(waiting, remaining),
                    "old_card_usable_only_while_unexpired": True},
         "evidence": [_citation(old_card, s) for s in old_segments]},
        {"id": "j1", "depends_on": ["c1", "u1"], "rule": "compare_derived_intervals",
         "inputs": {"waiting_from": "c1.output.usual_waiting_working_days",
                    "usable_from": "u1.output.old_card_interval_working_days"},
         "output": {"full_cycle_coverage_established": False if remaining < waiting else None},
         "evidence": [_citation(cycle, cycle_segments[1]), _citation(old_card, old_segments[1])]},
    ]
    return dict(result, status="derived", reason="HISTORICAL_ARCHIVE_COMPARISON_ONLY",
                conclusion="cannot_establish_full_cycle_coverage" if remaining < waiting else "coverage_not_disproved",
                usual_waiting_working_days=waiting,
                explanation=("假定同一起点与工作日口径，旧证剩余有效区间不足以覆盖来源描述的常规等待区间；"
                             "不据此认定实际领证日期或实际缺证天数。" if remaining < waiting else
                             "该区间比较未排除覆盖可能；不能据此保证实际领证或全程可用。"))

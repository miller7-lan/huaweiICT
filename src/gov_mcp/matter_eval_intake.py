"""Read-only reference intake. Technical binding never certifies independence."""
from pathlib import Path
import re
import unicodedata

from gov_mcp.knowledge_review import question_fingerprint
from gov_mcp.matter_graph import canonical_hash, file_hash, load_pack, scope_check
from gov_mcp.matter_guidance import TOPICS

PRIVATE = re.compile(r"\d{17}[\dXx]|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)")


def text(value, limit=2000):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and "\x00" not in value


def personal(value):
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    return bool(PRIVATE.search(normalized))


def inspect_submission(submission, pack_root, expected_commit):
    """Return codes/counts only. No freeze, predictions, model or publication API."""
    issues = []
    def issue(code, group=None):
        row = {"code": code}
        if group is not None: row["group_index"] = group
        if row not in issues: issues.append(row)
    report = {"schema": 1, "status": "REFERENCE_NOT_READY", "issues": issues,
        "reference_frozen": False, "independence_certified": False,
        "human_attribution_verified": False, "semantic_reference_review_completed": False,
        "current_source_applicability_checked": False, "predictions_executed": 0,
        "model_calls": 0, "can_execute_models": False, "formal_publication": False,
        "case_group_count": 0, "question_and_variant_count": 0, "bound_anchor_count": 0}
    if not isinstance(submission, dict): issue("SUBMISSION_OBJECT_REQUIRED"); return report
    try: report["submission_sha256"] = canonical_hash(submission)
    except (ValueError, TypeError): issue("CANONICAL_JSON_REQUIRED"); return report
    try:
        root = Path(pack_root); pack = load_pack(root)
        package_hash = file_hash((root / "manifest.json").read_bytes())
    except (ValueError, OSError, KeyError, TypeError):
        issue("SOURCE_PACKAGE_INTEGRITY_FAILED"); return report
    if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        issue("FULL_TARGET_COMMIT_REQUIRED")
    if submission.get("schema") != 1 or submission.get("status") != "SUBMITTED_FOR_REFERENCE_REVIEW":
        issue("SUBMITTED_REFERENCE_STATUS_REQUIRED")
    if not text(submission.get("reviewer"), 120): issue("EXTERNAL_REVIEWER_REQUIRED")
    if not text(submission.get("independence_declaration"), 3000): issue("INDEPENDENCE_DECLARATION_REQUIRED")
    if submission.get("reference_frozen_at") is not None: issue("USE_UNFROZEN_REFERENCE_INTAKE")
    if type(submission.get("predictions_executed")) is not int or submission["predictions_executed"] != 0:
        issue("PREDICTIONS_ALREADY_STARTED_OR_UNDECLARED")
    if submission.get("contains_real_applicant_identifiers") is not False: issue("ANONYMOUS_CASE_DECLARATION_REQUIRED")
    if submission.get("human_business_review") is not False or submission.get("formal_publication") is not False:
        issue("BUSINESS_APPROVAL_CANNOT_BE_GRANTED_BY_INTAKE")
    if submission.get("source_package_sha256") != package_hash: issue("SOURCE_PACKAGE_BINDING_REQUIRED")
    if submission.get("implementation_commit") != expected_commit: issue("TARGET_IMPLEMENTATION_COMMIT_MISMATCH")
    report.update(source_package_sha256=package_hash, implementation_commit=expected_commit)
    groups = submission.get("case_groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= 100:
        issue("EXTERNAL_CASE_GROUPS_REQUIRED"); return report
    report["case_group_count"] = len(groups)
    sources = {s["source_id"]: s for s in pack["sources"]}
    segments = {(s["source_id"], p["segment_id"]): p for s in pack["sources"] for p in s["segments"]}
    development = {question_fingerprint(row["question"]) for row in pack["development_tasks"]}
    development.update(question_fingerprint(row["label"]) for row in TOPICS.values())
    seen_ids, seen_questions = set(), set()
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict): issue("GROUP_OBJECT_REQUIRED", index); continue
        group_id = group.get("group_id")
        if not isinstance(group_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", group_id) or group_id in seen_ids:
            issue("UNIQUE_NONPERSONAL_GROUP_ID_REQUIRED", index)
        if isinstance(group_id, str): seen_ids.add(group_id)
        for field in ("origin_of_real_question", "development_overlap_disclosure", "reference_reasoning", "expected_answer_or_stop"):
            if not text(group.get(field)): issue("MISSING_" + field.upper(), index)
        if not isinstance(group.get("topic"), str) or group["topic"] not in TOPICS:
            issue("SUPPORTED_UI_TOPIC_REQUIRED", index)
        variants = group.get("variants", [])
        if not isinstance(variants, list) or len(variants) > 20:
            issue("BOUNDED_VARIANTS_REQUIRED", index); variants = []
        questions = [group.get("question"), *variants]
        for question in questions:
            if not text(question, 1000): issue("BOUNDED_QUESTION_REQUIRED", index); continue
            report["question_and_variant_count"] += 1
            if personal(question): issue("POSSIBLE_PERSONAL_DATA", index)
            key = question_fingerprint(question)
            if key in seen_questions: issue("DUPLICATE_NORMALIZED_QUESTION", index)
            if key in development: issue("EXACT_DEVELOPMENT_QUESTION_OVERLAP", index)
            seen_questions.add(key)
        for field in ("origin_of_real_question", "development_overlap_disclosure", "reference_reasoning", "expected_answer_or_stop"):
            if isinstance(group.get(field), str) and personal(group[field]): issue("POSSIBLE_PERSONAL_DATA", index)
        facts = group.get("declared_conditions")
        try:
            if not isinstance(facts, dict): raise ValueError()
            if any(isinstance(v, str) and (len(v) > 80 or personal(v)) for v in facts.values()): raise ValueError()
            for key in ("adult", "self", "old_card_valid", "urgent_exam", "fee_relief_requested"):
                if key in facts and facts[key] is not None and type(facts[key]) is not bool: raise ValueError()
            for key in ("region", "matter", "civil_capacity", "reason", "household"):
                if key in facts and facts[key] is not None and not isinstance(facts[key], str): raise ValueError()
            scope_check(pack, facts, require_old_card_valid=group.get("topic") == "old_card_lifecycle")
        except (ValueError, TypeError, KeyError): issue("ANONYMOUS_CONDITION_SCHEMA_REQUIRED", index)
        refs, urls = group.get("exact_reference_locators"), group.get("official_reference_urls")
        if group.get("reference_basis", "official_source") == "declared_scope_boundary":
            if refs != [] or urls != [] or group.get("scope_sha256") != canonical_hash(pack["scope"]):
                issue("DECLARED_SCOPE_REFERENCE_BINDING_REQUIRED", index)
            continue  # This is the project's scope boundary, never an official policy quote.
        if group.get("reference_basis", "official_source") != "official_source": issue("UNSUPPORTED_REFERENCE_BASIS", index)
        if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
            issue("EXACT_OFFICIAL_ANCHORS_REQUIRED", index); continue
        actual_urls = set()
        for ref in refs:
            if not isinstance(ref, dict): issue("BOUND_ANCHOR_OBJECT_REQUIRED", index); continue
            sid, segment_id = ref.get("source_id"), ref.get("segment_id")
            if not isinstance(sid, str) or not isinstance(segment_id, str): issue("BOUND_ANCHOR_OBJECT_REQUIRED", index); continue
            source, segment = sources.get(sid), segments.get((sid, segment_id))
            if (source is None or segment is None or not text(ref.get("quote"))
                or ref["quote"] not in segment["text"] or ref.get("locator") != segment["locator"]
                or ref.get("original_sha256") != source["original_sha256"]):
                issue("OFFICIAL_ANCHOR_BINDING_FAILED", index); continue
            actual_urls.add(source["url"]); report["bound_anchor_count"] += 1
        if not isinstance(urls, list) or any(not isinstance(u, str) for u in urls) or set(urls) != actual_urls:
            issue("OFFICIAL_URL_INDEX_MISMATCH", index)
    report["development_overlap_check"] = "normalized exact match only; semantic and group independence require external review"
    if not issues: report["status"] = "TECHNICAL_BINDING_READY_ATTRIBUTION_AND_FREEZE_STILL_REQUIRED"
    return report

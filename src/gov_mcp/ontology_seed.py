"""Build an engineering ontology candidate from one bounded public-source grammar.

The caller must obtain the input from ``knowledge_landing.retrieve`` so the
original and current source state have already been checked. This pure function
checks the returned source pins and exact text again; it neither authenticates an
arbitrary caller-supplied dictionary nor approves the source for business use.
One grammar family is the seed example. Numeric answers come from source text,
and threshold interpretation remains pending semantic review.
"""

import hashlib
import re

from gov_mcp.knowledge_landing import TARGETS, URLS


_HASH = re.compile(r"[a-f0-9]{64}")
_LOCATOR = re.compile(r"正文段落([1-9]\d{0,3})")
_HEADER = re.compile(
    r"(?:提示[：:])?本市户籍居民符合以下条件可在网上办理居民身份证损坏换领业务[：:]"
)
_CONTINUATION = re.compile(
    r"本市户籍居民[，,](?P<age>[1-9]\d{0,2})周岁以上[，,]"
    r"(?P<card>[1-9]\d?)年之内办理过居民身份证[，,]"
    r"(?P<photo>[1-9]\d?)年之内采集过居民身份证照片[，,]"
    r"至少有(?P<fingerprint>[一二两三四五六七八九十]|[1-9]\d?)枚指纹注册成功[。.]?"
)
_CHINESE_NUMBERS = dict(zip("一二三四五六七八九十", range(1, 11)), 两=2)
_PIN_FIELDS = ("source_id", "url", "revision_id", "original_sha256")


def _error(code):
    raise ValueError(code)


def _context_clauses(context, scope):
    if not isinstance(context, dict):
        _error("PUBLIC_SOURCE_CONTEXT_REQUIRED")
    source = context.get("source_id")
    if (not isinstance(source, str) or source not in TARGETS or TARGETS[source] != scope
            or context.get("url") != URLS[source]
            or not isinstance(context.get("revision_id"), str)
            or not re.fullmatch(r"rev-[A-Za-z0-9_-]{1,96}", context["revision_id"])
            or not isinstance(context.get("original_sha256"), str)
            or not _HASH.fullmatch(context["original_sha256"])):
        _error("PUBLIC_SOURCE_PINS_INVALID")
    clauses = context.get("selected_clauses")
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= 1000:
        _error("SOURCE_CLAUSES_REQUIRED")
    locators = set()
    for clause in clauses:
        if (not isinstance(clause, dict)
                or not isinstance(clause.get("locator"), str)
                or not _LOCATOR.fullmatch(clause["locator"])
                or clause["locator"] in locators
                or not isinstance(clause.get("text"), str)
                or not 1 <= len(clause["text"]) <= 20000):
            _error("SOURCE_CLAUSE_INVALID")
        locators.add(clause["locator"])
    return clauses


def _citation(context, clause):
    return {**{key: context[key] for key in _PIN_FIELDS},
            "locator": clause["locator"], "quote": clause["text"],
            "text_sha256": hashlib.sha256(clause["text"].encode("utf-8")).hexdigest()}


def _validate_ranked_citations(contexts, citations):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 5:
        _error("VERIFIED_RANKED_CITATION_REQUIRED")
    anchored = set()
    for citation in citations:
        if not isinstance(citation, dict):
            _error("RANKED_CITATION_INVALID")
        matches = [context for context in contexts
                   if all(citation.get(key) == context[key] for key in _PIN_FIELDS)]
        if len(matches) != 1:
            _error("RANKED_CITATION_PINS_DIFFER")
        context = matches[0]
        clause = next((row for row in context["selected_clauses"]
                       if row["locator"] == citation.get("locator")), None)
        if clause is None or any(citation.get(key) != value
                                 for key, value in _citation(context, clause).items()):
            _error("RANKED_CITATION_TEXT_DIFFERS")
        anchored.add(context["source_id"])
    if anchored != {context["source_id"] for context in contexts}:
        _error("UNANCHORED_SOURCE_CONTEXT")


def build_candidate(evidence_result):
    """Extract a pending channel relation; unsupported or ambiguous text fails shut.

    The header and immediately following selected paragraph must also have
    adjacent original paragraph ordinals. The entire continuation must fit the
    supported grammar, so qualifiers, omissions, negation and extra conditions
    cannot be silently dropped. Dates and professional applicability stay unknown.
    """
    if (not isinstance(evidence_result, dict)
            or evidence_result.get("mode") != "public_candidate_preview"
            or evidence_result.get("status") != "SOURCE_EXCERPTS"
            or evidence_result.get("formally_published") is not False
            or evidence_result.get("business_conclusion_status") != "PAUSED_UNREVIEWED"):
        _error("PUBLIC_CANDIDATE_PREVIEW_REQUIRED")
    region, matter = evidence_result.get("region"), evidence_result.get("matter")
    scope = (region, matter)
    if scope not in TARGETS.values():
        _error("SOURCE_SCOPE_UNSUPPORTED")
    contexts = evidence_result.get("source_context")
    if not isinstance(contexts, list) or not 1 <= len(contexts) <= 2:
        _error("PUBLIC_SOURCE_CONTEXT_REQUIRED")
    matches = []
    for context in contexts:
        clauses = _context_clauses(context, scope)
        for index, clause in enumerate(clauses):
            if _HEADER.fullmatch(clause["text"]):
                matches.append((context, clauses, index))
    if len({context["source_id"] for context in contexts}) != len(contexts):
        _error("DUPLICATE_SOURCE_CONTEXT")
    _validate_ranked_citations(contexts, evidence_result.get("citations"))
    if len(matches) != 1:
        _error("ONE_SUPPORTED_CHANNEL_HEADER_REQUIRED")
    context, clauses, index = matches[0]
    if index + 1 >= len(clauses):
        _error("CHANNEL_CONTINUATION_REQUIRED")
    # This grammar supports one final note and its single continuation only.
    # A later selected paragraph might add another condition or exception.
    if index + 2 != len(clauses):
        _error("CHANNEL_BLOCK_MUST_END_SELECTED_CONTEXT")
    header, continuation = clauses[index], clauses[index + 1]
    if (int(_LOCATOR.fullmatch(continuation["locator"]).group(1))
            != int(_LOCATOR.fullmatch(header["locator"]).group(1)) + 1):
        _error("ADJACENT_SOURCE_PARAGRAPHS_REQUIRED")
    parsed = _CONTINUATION.fullmatch(continuation["text"])
    if not parsed:
        _error("UNSUPPORTED_OR_INCOMPLETE_CHANNEL_CONDITIONS")
    values = parsed.groupdict()
    age, card, photo = (int(values[key]) for key in ("age", "card", "photo"))
    fingerprint = (_CHINESE_NUMBERS[values["fingerprint"]]
                   if values["fingerprint"] in _CHINESE_NUMBERS else int(values["fingerprint"]))
    if age > 150:
        _error("SOURCE_THRESHOLD_OUT_OF_RANGE")
    return {
        "schema_version": 1,
        "data_class": "public",
        "scope": {"region": region, "matter": matter, "branch": "damaged"},
        "entity_type": {"name": "ServiceChannel", "label": "办理渠道"},
        "relation_type": {"name": "available_via", "domain": "Matter",
                          "range": "ServiceChannel", "operator": "all"},
        "channel": "online",
        "conditions": [
            {"field": "local_household", "op": "eq", "value": True},
            {"field": "age_years", "op": "gte", "value": age},
            {"field": "card_years", "op": "lte", "value": card},
            {"field": "photo_years", "op": "lte", "value": photo},
            {"field": "fingerprint_count", "op": "gte", "value": fingerprint},
        ],
        "ambiguous_boundaries": ["age_years", "card_years", "photo_years"],
        "citations": [_citation(context, header), _citation(context, continuation)],
        "status": "pending_review",
        "extraction_method": "bounded_grammar_v1",
        "construction_metrics": {"seed_examples": 1, "source_documents": 1,
                                 "source_clauses": 2, "derived_conditions": 5,
                                 "threshold_values_from_source": 4, "cloud_calls": 0},
        "business_conclusion_status": "PAUSED_UNREVIEWED",
        "formally_published": False,
        "formal_approval": False,
        "current_applicability": "unknown",
    }

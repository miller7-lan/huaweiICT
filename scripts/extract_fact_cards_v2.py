"""Opt-in offline fixes for reviewed fact-card errors; the frozen v1 stays intact.

This candidate extractor is not connected to the QA flow. It retains v1's
objects/conditions, which still require human semantic review. It fixes only
age/week confusion, ordinal count references, and exact table-cell spans.
"""
import re

import extract_fact_cards as baseline

VERSION = "fact-cards-v2-candidate"
AGE_SUFFIX = re.compile(r"\s*岁")
ORDINAL_PREFIX = re.compile(r"(?:最后|第)\s*$")


def cell_spans(unit):
    """Map explicit addresses to spans in the serialized row; never first-match.

    Delimiters inside cells make this serialization ambiguous. Missing, repeated
    or misaligned addresses return no map instead of inventing a cell location.
    """
    addresses = unit.get("cells") or []
    pieces = unit["text"].split(" | ")
    if len(addresses) != len(pieces) or len(set(addresses)) != len(addresses):
        return {}
    spans, start = {}, 0
    for address, piece in zip(addresses, pieces):
        if re.fullmatch(r"[A-Z]+[1-9]\d*", address) is None:
            return {}
        spans[address] = (start, start + len(piece))
        start += len(piece) + 3
    return spans


def unit_cards(unit, columns=None):
    text = unit["text"]
    result = []
    for card in baseline.unit_cards(unit):
        start, end = card["span"]
        # v1 reads the 周 prefix in 18周岁 as weeks. Preserve the whole age unit.
        suffix = AGE_SUFFIX.match(text, end)
        if card["type"] == "duration" and card["value"]["unit"] == "周" and suffix:
            end = suffix.end()
            card = baseline.card(unit, "count", start, end,
                                 {"number": card["value"]["number"], "unit": "周岁"},
                                 baseline.around(text, baseline.sentences(text), start, end),
                                 baseline.around(text, [(m.start(), m.end()) for m in baseline.CLAUSE.finditer(text)], start, end))
        if card["type"] == "count" and card["value"]["unit"] == "次" \
                and ORDINAL_PREFIX.search(text[:start]):
            continue  # 最后一次 / 第一次 identify an occurrence, not a count limit.
        result.append({**card, "extractor": VERSION})
    spans = cell_spans(unit)
    for name, value, address in columns or []:
        span = spans.get(address)
        if span is None or text[span[0]:span[1]] != value:
            continue
        result.append({**baseline.card(unit, "table_cell", *span, {"column": name, "cell": address},
                                       (0, len(text))),
                       "subjects": baseline.mentions(text, baseline.SUBJECTS), "extractor": VERSION})
    return sorted(result, key=lambda c: (c["span"][0], c["type"]))


def extract(units):
    columns = baseline.table_columns(units)
    return [card for unit in units for card in unit_cards(unit, columns.get((unit["source_id"], unit["locator"])))]

"""Shared literal ranking only; publication, date and scope gates stay in the store."""

import re


VERSION = "literal-bigram-v1"


def query_grams(query):
    normalized = re.sub(r"\s+", "", query.lower())
    return {normalized[i:i+2] for i in range(max(1, len(normalized)-1))}


def score_literal(grams, text):
    return sum(term in text.lower() for term in grams)


def top_five(ranked):
    return [citation for _, citation in sorted(
        ranked, key=lambda item: (-item[0], item[1]["revision_id"], item[1]["locator"]))[:5]]

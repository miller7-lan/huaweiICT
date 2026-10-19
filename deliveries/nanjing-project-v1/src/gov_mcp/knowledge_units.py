"""Literal, reversible source-unit proposals; no matter or effectivity approval."""

import re

from gov_mcp.knowledge import fingerprint


PAGE = r"^PDF第([1-9][0-9]{0,2})页(?:（OCR待核对）)?$"
HEADING = r"^\s*(?:[一二三四五六七八九十百]{1,4}\s*、|、)\s*\S"
LIMITS = {"chunks": 256, "characters_per_chunk": 16000, "units": 256,
          "spans_per_unit": 4, "characters_per_unit": 16000, "heading_characters": 120}
VERSION = "literal-source-units-v1-"+fingerprint([PAGE, HEADING, LIMITS,
    "exact_last_line_physical_page_number", "blank_page_break", "new_numbered_sequence_title_break"])[:12]


def propose(chunks):
    if not isinstance(chunks, list) or any(
        not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in ("locator", "category", "text"))
        for c in chunks
    ):
        raise ValueError("Bounded canonical source chunks required")
    body_sha = fingerprint(chunks)
    result = {"algorithm_version": VERSION, "status": "machine_proposal_not_approved",
        "body_sha256": body_sha, "offset_unit": "unicode_code_points", "units": [], "omitted_spans": [],
        "coverage_complete": True, "requires_visual_review": False, "issues": [],
        "scope_inferred": False, "material_obligations": "unknown", "current_effectivity": "unknown",
        "human_review_completed": False, "business_release_ready": False,
        "text_join_separator": "\n", "joined_text_is_not_a_contiguous_source_quote": True}
    if not chunks:
        result.update(status="no_source_chunks", issues=["empty_subset_no_units"])
        return result

    def incomplete(reason):
        result.update(status="manual_review_required", coverage_complete=False, units=[], omitted_spans=[], issues=[reason])
        return result

    if (len(chunks) > LIMITS["chunks"] or len({c["locator"] for c in chunks}) != len(chunks)
            or any(not c["text"] or len(c["text"]) > LIMITS["characters_per_chunk"] for c in chunks)):
        return incomplete("source_input_limit_or_duplicate_locator")
    text_hashes = [fingerprint(c["text"].encode("utf-8")) for c in chunks]

    def span(index, start, end):
        return {"parent_ordinal": index, "locator": chunks[index]["locator"], "range": [start, end],
            "parent_text_sha256": text_hashes[index],
            "span_text_sha256": fingerprint(chunks[index]["text"][start:end].encode("utf-8"))}

    def new_unit(part, heading=None, flags=None):
        value = {"spans": [part], "heading_surface": heading, "flags": flags or []}
        result["units"].append(value)
        return value

    current, previous_page = None, None
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        page_match = re.fullmatch(PAGE, chunk["locator"])
        if not page_match:
            new_unit(span(index, 0, len(text)), flags=["canonical_chunk_kept"])
            current, previous_page = None, None
            continue
        result["requires_visual_review"] = True
        page = int(page_match[1])
        if previous_page is not None and page != previous_page+1:
            result["issues"].append("nonadjacent_pages_not_joined")
        if previous_page != page-1:
            current = None
        lines, offset, headings = text.splitlines(keepends=True), 0, []
        body_end = len(text)
        # Only this exact footer is omitted from derived indexing; its source span remains recorded.
        if lines and lines[-1].strip() == str(page):
            body_end -= len(lines[-1])
            result["omitted_spans"].append({**span(index, body_end, len(text)), "reason": "exact_physical_page_number_footer"})
        for line in lines:
            surface = line.rstrip("\r\n")
            if offset < body_end and len(surface) <= LIMITS["heading_characters"] and re.match(HEADING, surface) and not surface.rstrip().endswith(("。", "；", ";")):
                headings.append((offset, surface))
            offset += len(line)
        prefix_end = headings[0][0] if headings else body_end
        if prefix_end:
            part = span(index, 0, prefix_end)
            prefix_lines = [line.strip() for line in text[:prefix_end].splitlines() if line.strip()]
            new_sequence_title = (headings and re.match(r"^\s*一\s*、", headings[0][1])
                and len(prefix_lines) == 1 and len(prefix_lines[0]) <= LIMITS["heading_characters"]
                and not prefix_lines[0].endswith(("。", "；", ";", "：", ":")))
            if current and new_sequence_title:
                current = None
                result["issues"].append("new_numbered_sequence_title_not_joined")
            size = sum(s["range"][1]-s["range"][0] for s in current["spans"]) if current else 0
            if current and current["heading_surface"] and len(current["spans"]) < LIMITS["spans_per_unit"] and size+prefix_end <= LIMITS["characters_per_unit"]:
                current["spans"].append(part)
                current["flags"].append("cross_page_continuation_proposed")
            else:
                flags = ["page_prefix_kept"]
                if current and current["heading_surface"]:
                    flags.append("continuation_limit_requires_review")
                    result["issues"].append("continuation_limit_requires_review")
                current = new_unit(part, flags=flags)
        for position, (start, surface) in enumerate(headings):
            end = headings[position+1][0] if position+1 < len(headings) else body_end
            flags = ["literal_heading_boundary_proposed"]
            if surface.lstrip().startswith("、"):
                flags.append("ocr_heading_number_missing_not_corrected")
            current = new_unit(span(index, start, end), heading=surface, flags=flags)
        if len(result["units"]) > LIMITS["units"]:
            return incomplete("unit_count_limit")
        if not body_end:
            current = None
            result["issues"].append("footer_only_page_break")
        previous_page = page

    # Every original code point must be accounted for, including omitted footer spans.
    for index, chunk in enumerate(chunks):
        ranges = sorted(s["range"] for u in result["units"] for s in u["spans"] if s["parent_ordinal"] == index)
        ranges += [s["range"] for s in result["omitted_spans"] if s["parent_ordinal"] == index]
        cursor = 0
        for start, end in sorted(ranges):
            if start != cursor or end <= start or end > len(chunk["text"]):
                raise ValueError("Source-unit coverage gap or overlap")
            cursor = end
        if cursor != len(chunk["text"]):
            raise ValueError("Source-unit coverage incomplete")
    for unit in result["units"]:
        joined = "\n".join(chunks[s["parent_ordinal"]]["text"][slice(*s["range"])] for s in unit["spans"])
        unit["text_sha256"] = fingerprint(joined.encode("utf-8"))
        unit["flags"] = sorted(set(unit["flags"]))
        unit["id"] = "U-"+fingerprint([VERSION, body_sha, unit])[:24]
    result["issues"] = sorted(set(result["issues"]))
    return result


def materialize(proposal, chunks):
    if proposal != propose(chunks) or not proposal["coverage_complete"]:
        raise ValueError("Source-unit proposal is changed, incomplete or bound to other source text")
    values = []
    for unit in proposal["units"]:
        parts = [{"locator": s["locator"], "range": s["range"],
            "text": chunks[s["parent_ordinal"]]["text"][slice(*s["range"])]} for s in unit["spans"]]
        values.append({**unit, "parts": parts, "text_for_ranking": "\n".join(p["text"] for p in parts)})
    return values

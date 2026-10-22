import copy

import pytest

from gov_mcp.knowledge_units import LIMITS, materialize, propose


def page(number, text):
    return {"locator": f"PDF第{number}页（OCR待核对）", "category": "procedure", "text": text}


def test_empty_changed_subset_has_no_units_without_claiming_publication_readiness():
    p = propose([])
    assert p["status"] == "no_source_chunks" and p["units"] == []
    assert materialize(p, []) == []
    assert p["business_release_ready"] is False


def test_multimatter_sections_split_and_cross_page_section_keeps_both_sources():
    chunks = [page(1, "文件标题\n一、事项甲\n甲办理条件。\n、事项乙标题\n1"),
              page(2, "乙的办理条件与时限。\n四、事项丙\n丙办理条件。\n2")]
    proposal = propose(chunks)
    units = materialize(proposal, chunks)
    selected = next(u for u in units if u["heading_surface"] == "、事项乙标题")
    assert len(selected["parts"]) == 2
    assert {p["locator"] for p in selected["parts"]} == {c["locator"] for c in chunks}
    assert "甲办理" not in selected["text_for_ranking"] and "丙办理" not in selected["text_for_ranking"]
    assert "乙的办理条件" in selected["text_for_ranking"]
    assert "ocr_heading_number_missing_not_corrected" in selected["flags"]
    assert "二、" not in selected["text_for_ranking"]
    assert proposal["coverage_complete"] and len(proposal["omitted_spans"]) == 2
    assert proposal["material_obligations"] == proposal["current_effectivity"] == "unknown"
    assert proposal["scope_inferred"] is False and proposal["human_review_completed"] is False


def test_original_characters_can_be_reconstructed_including_footer_and_whitespace():
    chunks = [page(1, " 标题\r\n一 、 事项甲\r\n甲正文。\r\n1\r\n"), page(2, "后续正文。\n2")]
    p = propose(chunks)
    for index, original in enumerate(chunks):
        spans = [s for u in p["units"] for s in u["spans"] if s["parent_ordinal"] == index]
        spans += [s for s in p["omitted_spans"] if s["parent_ordinal"] == index]
        recovered = "".join(original["text"][slice(*s["range"])] for s in sorted(spans, key=lambda s: s["range"]))
        assert recovered == original["text"]


def test_only_exact_last_physical_page_number_is_omitted():
    chunks = [page(1, "一、事项甲\n金额5元。\n2"), page(2, "正文。\n2\n后续行。")]
    p = propose(chunks)
    assert p["omitted_spans"] == []
    assert "金额5元" in materialize(p, chunks)[0]["text_for_ranking"]


@pytest.mark.parametrize("chunks", [
    [page(1, "一、事项甲\n正文。\n1"), page(3, "不能假定为上一页续文。\n3")],
    [page(1, "一、事项甲\n正文。\n1"), page(2, "2"), page(3, "不能越过只有页码的页面。\n3")],
])
def test_missing_or_footer_only_page_does_not_join_unproven_continuation(chunks):
    p = propose(chunks)
    assert all(len(u["spans"]) == 1 for u in p["units"])
    assert p["issues"]


def test_html_clause_is_not_split_by_incidental_numbered_lines():
    chunks = [{"locator": "第十条", "category": "identity", "text": "正文。\n一、同一条款条件甲\n二、同一条款条件乙"}]
    units = materialize(propose(chunks), chunks)
    assert len(units) == 1 and units[0]["text_for_ranking"] == chunks[0]["text"]


def test_new_attachment_title_before_restarted_numbering_is_not_appended_to_old_section():
    chunks = [page(1, "四、通知要求\n此处保留通知正文\n1"),
              page(2, "附件新标题\n一、另一事项\n附件正文。\n2")]
    p = propose(chunks)
    values = materialize(p, chunks)
    old = next(v for v in values if v["heading_surface"] == "四、通知要求")
    assert "附件新标题" not in old["text_for_ranking"]
    assert "new_numbered_sequence_title_not_joined" in p["issues"]


def test_long_cross_page_section_has_bounded_spans_without_losing_source_text():
    chunks = [page(i, ("一、事项甲\n" if i == 1 else "")+"正文。\n"+str(i)) for i in range(1, 7)]
    p = propose(chunks)
    assert p["coverage_complete"]
    assert all(len(u["spans"]) <= LIMITS["spans_per_unit"] for u in p["units"])
    assert "continuation_limit_requires_review" in p["issues"]


def test_tampered_offsets_labels_or_changed_source_cannot_materialize():
    chunks = [page(1, "一、事项甲\n正文。\n1")]
    p = propose(chunks)
    for key, value in (("heading_surface", "伪造标题"), ("spans", [])):
        changed = copy.deepcopy(p)
        changed["units"][0][key] = value
        with pytest.raises(ValueError, match="changed"):
            materialize(changed, chunks)
    changed_chunks = copy.deepcopy(chunks)
    changed_chunks[0]["text"] = changed_chunks[0]["text"].replace("正文", "新文")
    with pytest.raises(ValueError, match="changed"):
        materialize(p, changed_chunks)


def test_limit_or_duplicate_locator_yields_explicit_incomplete_proposal():
    chunks = [page(1, "甲"*(LIMITS["characters_per_chunk"]+1))]
    p = propose(chunks)
    assert p["coverage_complete"] is False and p["units"] == []
    with pytest.raises(ValueError, match="incomplete"):
        materialize(p, chunks)
    p = propose([page(1, "甲"), page(1, "乙")])
    assert p["status"] == "manual_review_required" and not p["coverage_complete"]

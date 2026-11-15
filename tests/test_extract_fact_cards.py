"""Rule-based fact cards: spans, types, subjects, sampling and scoring (synthetic and repository data)."""
import json

import pytest

import build_frozen_corpus as builder
import extract_fact_cards as cards_tool

SPEC = {"code": "A", "source_id": "DOC"}


def unit(locator, text, kind="html_paragraph", **extra):
    return builder.unit(SPEC, locator, text, kind, **extra)


def by_type(cards, kind):
    return [(c["text"], c["value"]) for c in cards if c["type"] == kind]


def test_dates_years_and_document_numbers_are_never_read_as_quantities():
    cards = cards_tool.extract([unit("段1", "自2018年4月1日起停征首次申领工本费，依据财综[2007]34号；2021年通知规定15个工作日。")])
    assert by_type(cards, "date") == [("2018年4月1日", {"date": "2018-04-01", "role": "unspecified"})]
    assert by_type(cards, "document_number") == [("财综[2007]34号", {"number": "财综[2007]34号", "key": "财综〔2007〕34号"})]
    assert by_type(cards, "year") == [("2021年", "2021")]
    assert by_type(cards, "duration") == [("15个工作日", {"number": "15", "unit": "工作日"})]
    assert by_type(cards, "amount") == [] and by_type(cards, "count") == []


def test_amounts_take_the_subjects_and_qualifiers_of_their_own_clause():
    text = "向首次申领、换领的居民收取工本费每证20元；向遗失补领、损坏换领的每证40元。申领时不得代办，15个工作日内办结。"
    cards = {c["text"]: c for c in cards_tool.extract([unit("段1", text)])}
    assert cards["20元"]["subjects"] == ["首次申领", "换领"] and cards["20元"]["subjects_from"] == "clause"
    assert cards["40元"]["subjects"] == ["损坏换领", "遗失补领"]
    assert cards["15个工作日"]["qualifiers"] == []  # 不得 belongs to the clause before it
    assert cards["15个工作日"]["subjects_from"] == "sentence"


def test_typed_and_metadata_dates_keep_their_role():
    cards = cards_tool.extract([unit("段1", "本办法自2019年1月1日起施行。"),
                                unit("网页发布日期", "2026-05-05", "page_publication_date")])
    roles = sorted(c["value"]["role"] for c in cards if c["type"] == "date")
    assert roles == ["effective_date", "page_publication_date"]


def test_table_cells_carry_their_column_names():
    header = unit("Excel工作表证件!第4行", "项目名称 | 设立依据 | 收费标准", "xlsx_row", cells=["D4", "F4", "G4"])
    row = unit("Excel工作表证件!第36行", "居民身份证工本费 | 财综[2007]34号 | 20元", "xlsx_row", cells=["D36", "F36", "G36"])
    cards = cards_tool.extract([header, row])
    cells = {c["value"]["column"]: c["text"] for c in cards if c["type"] == "table_cell"}
    assert cells == {"项目名称": "居民身份证工本费", "设立依据": "财综[2007]34号", "收费标准": "20元"}


def test_every_card_points_at_its_exact_text_and_the_ids_are_stable():
    units = [unit("段1", "向首次申领的居民收取工本费每证20元，有效期三个月，年满16岁。")]
    cards = cards_tool.extract(units)
    assert cards_tool.verify_cards(cards, units) == [] and cards == cards_tool.extract(units)
    assert cards_tool.verify_cards(cards, [unit("段1", "已改动的文字")]) == [c["card_id"] for c in cards]
    summary = cards_tool.summarize(cards, units)
    assert summary["span_mismatches"] == 0 and summary["cards"] == len(cards) and "20元" not in json.dumps(summary)


def test_the_real_scan_and_catalogue_title_produce_the_expected_cards():
    spec = {"code": "A3", "source_id": "HN-MEASURES-2021-PDF"}
    units = builder.scan_units(spec) + builder.catalog_title_unit(spec, builder.catalog_documents()[spec["source_id"]])
    cards = cards_tool.extract(units)
    assert ("40日", {"number": "40", "unit": "日"}) in by_type(cards, "duration")
    assert ("30日", {"number": "30", "unit": "日"}) in by_type(cards, "duration")
    assert [value["key"] for _, value in by_type(cards, "document_number")] == ["豫公〔2021〕16号"]
    assert cards_tool.verify_cards(cards, units) == []


def test_sample_is_stratified_and_score_accepts_only_marked_rows():
    units = [unit(f"段{i}", f"向首次申领的居民收取工本费每证{i}元，{i}个工作日内办结。") for i in range(1, 6)]
    cards = cards_tool.extract(units)
    chosen = cards_tool.sample(cards, 2, "s")
    assert sorted(c["type"] for c in chosen) == ["amount", "amount", "duration", "duration"]
    assert chosen == cards_tool.sample(cards, 2, "s")
    rows = [{"card_id": c["card_id"], "correct": "是"} for c in chosen]
    problems, counts = cards_tool.score(rows, cards)
    assert problems == [] and counts["amount"] == {"是": 2, "checked": 2}
    rows[0]["correct"] = ""
    rows.append({"card_id": "x" * 16, "correct": "是"})
    problems, _ = cards_tool.score(rows, cards)
    assert len(problems) == 2


def test_flow_masks_a_document_number_written_with_a_lead_word():
    from gov_mcp import frozen_qa_flow_v3 as flow
    assert flow._document_number_key("依据财综[2007]34号") == flow._document_number_key("财综〔2007〕34号")
    assert flow._document_number_key("财综〔2007〕35号") != flow._document_number_key("财综〔2007〕34号")

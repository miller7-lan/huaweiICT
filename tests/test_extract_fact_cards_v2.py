"""Candidate fixes verified offline, including guards against lost valid facts."""
import pytest

import extract_fact_cards as baseline
import extract_fact_cards_v2 as candidate
from test_extract_fact_cards import unit


@pytest.mark.parametrize("text,number", [("未满18周岁", "18"), ("十六周岁以上", "十六"), ("满8周 岁", "8")])
def test_age_keeps_complete_unit_and_never_becomes_weeks(text, number):
    cards = candidate.extract([unit("段1", text)])
    assert not any(c["type"] == "duration" for c in cards)
    age = next(c for c in cards if c["type"] == "count")
    assert age["value"] == {"number": number, "unit": "周岁"}
    assert age["text"] == text[age["span"][0]:age["span"][1]]


@pytest.mark.parametrize("text", ["最后一次回国", "第1次审查意见", "第一次办理"])
def test_ordinal_reference_is_not_a_count_requirement(text):
    assert not any(c["type"] == "count" for c in candidate.extract([unit("段1", text)]))


def test_valid_weeks_frequency_and_age_remain_and_v1_is_unchanged():
    item = unit("段1", "2周内办结。每年签注一次。应当一次性告知。年满16岁。")
    cards = candidate.extract([item])
    assert [(c["text"], c["type"]) for c in cards] == [
        ("2周", "duration"), ("一次", "count"), ("一次", "count"), ("16岁", "count")]
    assert baseline.VERSION == "fact-cards-v1"
    assert all(c["extractor"] == candidate.VERSION for c in cards)


def test_duplicate_cell_values_bind_their_own_addresses_and_spans():
    header = unit("Excel工作表证件!第1行", "项目名称 | 设立依据 | 收费标准", "xlsx_row", cells=["C1", "D1", "E1"])
    row = unit("Excel工作表证件!第2行", "考试 | 财税〔2018〕66号，发改价格〔2015〕1217号 | 发改价格〔2015〕1217号",
               "xlsx_row", cells=["C2", "D2", "E2"])
    cards = candidate.extract([header, row])
    cell = next(c for c in cards if c["type"] == "table_cell" and c["value"]["cell"] == "E2")
    assert cell["span"][0] == row["text"].rindex("发改价格〔2015〕1217号")
    assert baseline.verify_cards(cards, [header, row]) == []


@pytest.mark.parametrize("addresses", [["D2"], ["D2", "D2"], ["D2", "invalid"]])
def test_ambiguous_or_invalid_cell_mapping_produces_no_cell_claim(addresses):
    row = unit("Excel工作表证件!第2行", "甲 | 甲", "xlsx_row", cells=addresses)
    assert candidate.unit_cards(row, [("收费标准", "甲", "D2")]) == []

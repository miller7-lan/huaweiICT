"""Flow V3.1 claim binding and scope gate v2 against the synthetic comparison bench (no model call)."""
import sys
from pathlib import Path

import pytest

from gov_mcp import claim_binding as cb, scope_gate_v2

BENCH = Path(__file__).resolve().parents[1] / "evaluation" / "refactor-bench-20261115"
sys.path.insert(0, str(BENCH))
import run_bench  # noqa: E402

RESULT = run_bench.run(Path(__file__).resolve().parents[1] / "src", "A")
# Held-out errors this lexical design is known to miss; a semantic check is needed for them.
KNOWN_MISSES = {"B02_extension_number", "B06_condition_outside_lexicon", "B09_wrong_start"}


@pytest.mark.parametrize("row", [r for r in RESULT["rows"] if r["id"] not in KNOWN_MISSES], ids=lambda r: r["id"])
def test_bench_case(row):
    assert row["ok"], row
    if row["kind"] == "good":
        assert row["direct"], row


def test_known_misses_stay_documented():
    missed = {r["id"] for r in RESULT["rows"] if not r["ok"]}
    assert missed <= KNOWN_MISSES


@pytest.mark.parametrize("text, value", [("二十", "20"), ("一百零五", "105"), ("二〇〇七", "2007"), ("两", "2"),
                                         ("十五", "15"), ("三万", "30000"), ("20", "20"), ("1,000", "1000")])
def test_numbers(text, value):
    assert cb.number_value(text) == value


def test_quantities_skip_dates_years_document_numbers_and_cells():
    text = "2024年3月1日起，依据财综[2007]34号，G36记载工本费20元，有效期三个月，第3行，拍照三次，一次性告知。"
    assert cb.quantities(text) == {("20", "元"), ("3", "月"), ("3", "次")}


def test_document_number_key_keeps_the_issuer():
    assert cb.document_number_key("依据财综[2007]34号") == cb.document_number_key("财综〔2007〕34号")
    assert cb.document_number_key("豫财综〔2007〕34号") != cb.document_number_key("财综〔2007〕34号")


def test_unknowns_with_numbers_are_not_displayed():
    assert cb.displayable_unknowns(["邮费", "费用为60元", "第3项"]) == (["邮费"], 2)


def test_gate_keeps_the_first_gate_outcomes():
    from gov_mcp import frozen_qa
    for question in ("南京换领身份证多少钱？", "今天换领要多少钱？", "123456200001010012能办吗", "郑州换领工本费是多少？"):
        assert scope_gate_v2.scope_gate(question) == frozen_qa.scope_gate(question)

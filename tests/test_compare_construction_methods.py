"""Construction-method comparison: normalization, gates, bindings and voting (synthetic data)."""
import compare_construction_methods as compare

TEXTS = [{"source_id": "DOC-A", "locator": "正文段落4",
          "text": "向首次申领、换领的居民收取工本费每证20元；向遗失补领、损坏换领的居民收取每证40元。"}]


def candidate(subject, predicate, obj, quote, qualifiers=()):
    return {"kind": "value", "subject": subject, "predicate": predicate, "object": obj,
            "qualifiers": list(qualifiers), "quote": quote}


def test_norm_writes_dates_and_chinese_counts_as_numbers():
    assert compare.norm("年满十六周岁之日起三个月内") == "年满16周岁之日起3月内"
    assert compare.norm("2022年9月1日") == compare.norm("2022-09-01") == "2022-9-1"
    assert compare.norm("第二百三十四条") == "第234条"
    assert compare.norm("第二代居民身份证") == "第二代居民身份证"


def test_elements_take_each_span_once():
    found = compare.elements([{"text": "于2007年6月底前报送，执行财综〔 2004〕8号及《某某通知》，每证10元。"}])
    assert found == ["10元", "2007年6月底", "某某通知", "财综〔2004〕8号"]


def test_gate_rejects_inexact_quotes_untraceable_numbers_and_duplicates():
    marked = compare.gate([
        candidate("换领", "has_fee", "20元", "换领的居民收取工本费每证20元"),
        candidate("换领", "has_fee", "20元", "换领的居民收取工本费每证20元"),
        candidate("换领", "has_fee", "25元", "换领的居民收取工本费每证20元"),
        candidate("换领", "has_fee", "20元", "换领收取20元"),
        candidate("补领", "has_fee", "40元", "每证40元", ["正文段落4"]),
    ], TEXTS)
    assert [c["accepted"] for c in marked] == [True, False, False, False, True]
    assert marked[1]["duplicate"] and not marked[2]["numbers_traceable"] and not marked[3]["quote_exact"]


def test_bindings_count_branch_value_mixups():
    good = candidate("损坏换领", "has_fee", "40元", "每证40元")
    wrong = candidate("损坏换领", "has_fee", "20元", "每证20元")
    assert compare.bindings("U4", [good]) == (1, 0)
    assert compare.bindings("U4", [good, wrong]) == (1, 1)
    assert compare.bindings("U6", [candidate("办理周期", "has_time_limit", "30个工作日", "压缩至30日")]) == (0, 1)


def test_vote_keeps_signatures_seen_twice():
    first = candidate("换领", "has_fee", "20元", "每证20元")
    other = candidate("补领", "has_fee", "40元", "每证40元")
    kept, low = compare.vote([[first, other], [first], []], ["20元", "40元"])
    assert kept == [first] and low == [other]

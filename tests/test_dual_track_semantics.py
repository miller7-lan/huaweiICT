"""Exact semantic provenance, unknown handling and cross-source non-leakage."""
import pytest

import extract_fact_cards_v3 as semantic
from test_extract_fact_cards import unit


def select(cards, text):
    return next(c for c in cards if c["text"] == text)


def test_conditions_and_modalities_have_separate_exact_references():
    item = unit("段1", "居住证持有人在居住地连续居住的，应当在居住证签发每满1年之日前1个月内办理签注手续。")
    cards = semantic.extract([item])
    card = select(cards, "1个月")
    assert "居住证" in card["subjects"] and "签注" in card["subjects"]
    assert "应当" not in card["qualifiers"]
    assert any(c["text"] == "应当" for c in card["field_evidence"]["modalities"])
    assert any("在居住地连续居住" in c for c in card["qualifiers"])
    assert semantic.verify_field_references(cards, [item]) == []
    assert all(c["review_state"] == "machine_candidate_unreviewed" for c in cards)


def test_changes_keep_old_new_values_and_context_years_without_claiming_current_validity():
    item = unit("段1", "2020年，居民身份证办理时限由10个工作日缩短至7个工作日。2022年，居民身份证在5日内制作。")
    card = select(semantic.extract([item]), "7个工作日")
    change = card["field_evidence"]["changes"][0]
    assert change["before"]["text"] == "10个工作日" and change["after"]["text"] == "7个工作日"
    assert change["card_stage"] == "after"
    assert len(card["field_evidence"]["time_context"]) == 2
    assert card["current_applicability"] == "unknown" and not card["formal_approval"]


def test_dates_distinguish_start_end_and_landing_document_identity():
    items = [unit("段1", "自2020年1月1日至2021年12月31日，暂免征收证券期货行业机构监管费。"),
             unit("段2", "自2019年4月1日起，停征首次申领居民身份证工本费。"),
             unit("段3", "本实施规定自2022年9月1日起施行。")]
    cards = semantic.extract(items)
    assert select(cards, "2020年1月1日")["value"]["role"] == "fee_exemption_start_date"
    assert select(cards, "2021年12月31日")["value"]["role"] == "fee_exemption_end_date"
    assert select(cards, "2019年4月1日")["value"]["role"] == "fee_suspension_start_date"
    assert "本实施规定" in select(cards, "2022年9月1日")["subjects"]


def test_unknown_date_and_object_are_exposed_instead_of_guessed():
    card = select(semantic.extract([unit("段1", "2019年2月3日，材料说明另附。")]), "2019年2月3日")
    assert card["value"]["role"] == "unspecified"
    assert "OBJECT_NOT_BOUND" in card["semantic_gaps"] and "DATE_ROLE_NOT_ESTABLISHED" in card["semantic_gaps"]


def test_full_document_key_preserves_issuer_and_legacy_key_is_separate():
    card = next(c for c in semantic.extract([unit("段1", "豫发改收费〔2020〕12号")]) if c["type"] == "document_number")
    assert card["value"]["key"] == "豫发改收费〔2020〕12号"
    assert card["value"]["legacy_match_key"] != card["value"]["key"]


def test_announcement_and_creation_years_name_the_document_instead_of_fee_effective_year():
    cards = semantic.extract([unit("段1", "依据财政部公告2019年第76号。2006年制定的《登记管理规范》废止。")])
    assert select(cards, "2019年")["year_role"] == "announcement_year"
    created = select(cards, "2006年")
    assert created["year_role"] == "document_creation_year" and created["subjects"] == ["《登记管理规范》"]


def test_notification_and_information_bundle_are_not_count_limits():
    cards = semantic.extract([unit("段1", "申请材料不全的，应当一次性告知需补充材料。扫码可一次性了解窗口相关业务所有办理信息。")])
    counts = [c for c in cards if c["type"] == "count"]
    assert [c["quantity_interpretation"] for c in counts] == ["notification_completeness", "information_bundle"]
    assert counts[0]["field_evidence"]["conditions"] and counts[1]["subjects"]


def test_table_parent_context_cannot_cross_source_or_revision():
    header = unit("Excel工作表费用!第1行", "项目序号 | 项目名称 | 设立依据 | 收费标准", "xlsx_row", cells=["C1","D1","F1","G1"])
    parent = unit("Excel工作表费用!第2行", "1 | 登记费 | 豫发改收费〔2020〕12号", "xlsx_row", cells=["C2","D2","F2"])
    child = unit("Excel工作表费用!第3行", "（1）首次登记费", "xlsx_row", cells=["D3"])
    foreign = {**child, "source_id": "OTHER"}
    cards = semantic.extract([header,parent,child,foreign])
    c = next(c for c in cards if c["type"] == "table_cell" and c["value"]["cell"] == "D3")
    assert c["subjects"] == ["（1）首次登记费", "登记费"]
    assert not any(c["type"] == "table_cell" and c["source_id"] == "OTHER" for c in cards)
    assert semantic.verify_field_references(cards, [header,parent,child,foreign]) == []
    with pytest.raises(ValueError,match='MULTIPLE_SOURCE_VERSIONS'):
        semantic.extract([header, parent, {**child, "revision_id":"other"}])


def test_conflicting_serialized_versions_are_rejected():
    item = unit("段1", "时限3日。")
    with pytest.raises(ValueError, match="AMBIGUOUS_SOURCE"):
        semantic.extract([item, {**item, "text":"时限7日。", "revision_id":"v2"}])


def test_manifest_does_not_silently_replace_or_misattribute_originals():
    item=unit("段1","时限3日。",original_sha256="a"*64)
    source={"source_id":"DOC","original_sha256":"b"*64}
    with pytest.raises(ValueError,match="MANIFEST_BINDING"):
        semantic.extract([item],[source])
    with pytest.raises(ValueError,match="AMBIGUOUS_SOURCE_MANIFEST"):
        semantic.extract([item],[source,source])


def test_nested_table_items_keep_the_immediate_parent_and_explicit_context():
    header=unit("Excel工作表费用!第1行","项目序号 | 项目名称 | 设立依据 | 收费标准","xlsx_row",cells=["C1","D1","F1","G1"])
    parent=unit("Excel工作表费用!第2行","1 | 证照费 | 规范甲 | 规范甲","xlsx_row",cells=["C2","D2","F2","G2"])
    subgroup=unit("Excel工作表费用!第3行","（1）户籍管理工本费（限于补办）","xlsx_row",cells=["D3"])
    child=unit("Excel工作表费用!第4行","①居民户口簿","xlsx_row",cells=["D4"])
    cards=semantic.extract([header,parent,subgroup,child])
    c=next(c for c in cards if c['type']=='table_cell' and c['value']['cell']=='D4')
    assert c['subjects']==['①居民户口簿','（1）户籍管理工本费（限于补办）']
    assert semantic.verify_field_references(cards,[header,parent,subgroup,child])==[]


def test_age_in_a_name_change_rule_has_an_explicit_matter_reference():
    item=unit('段1','18周岁以下人员变更姓名的，由父母提出申请。')
    card=select(semantic.extract([item]),'18周岁')
    assert '变更姓名' in card['subjects'] and card['value']['unit']=='周岁'
    assert 'OBJECT_NOT_BOUND' not in card['semantic_gaps']

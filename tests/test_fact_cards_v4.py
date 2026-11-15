import pytest
import extract_fact_cards_v4 as v4
from test_extract_fact_cards import unit


def card(text, value):
    u = unit('段1', text)
    cards = v4.extract([u])
    assert v4.verify_field_references(cards, [u]) == []
    assert all(c['review_state']=='machine_candidate_unreviewed' and
               c['current_applicability']=='unknown' and not c['formal_approval'] for c in cards)
    return next(c for c in cards if c['text']==value)


def test_fees_do_not_leak_across_distinct_clauses_or_document_title():
    text='依据《居民身份证收费通知》，即向首次申领、换领第二代居民身份证的居民收取工本费每证20元；向遗失补领第二代居民身份证的居民收取工本费每证40元。'
    assert card(text,'20元')['subjects']==['首次申领、换领第二代居民身份证']
    assert card(text,'40元')['subjects']==['遗失补领第二代居民身份证']


def test_action_is_completed_work_not_application_trigger():
    c=card('2025年，群众在派出所申领、补领、换领居民身份证后，公安机关在22日内完成居民身份证的审核、签发、制作，并发放至派出所。','22日')
    f=c['field_evidence']
    assert any('审核、签发、制作，并发放' in r['text'] for r in f['actions'])
    assert not any(r['text'].endswith('身份证后') for r in f['actions'])
    assert any(r['text'].endswith('身份证后') for r in f['starting_events'])


def test_waiting_start_is_notice_not_expiry():
    c=card('书面告知其相关户口政策；告知3个工作日后仍不提交居民户口簿的，可以申请。','3个工作日')
    f=c['field_evidence']
    assert [r['text'] for r in f['starting_events']]==['书面告知其相关户口政策']
    assert f['waiting_endpoints'][0]['text']=='告知3个工作日后'
    assert '告知3个工作日后' not in c['qualifiers']


def test_unstated_notice_is_not_invented():
    c=card('告知3个工作日后仍不提交居民户口簿的，可以申请。','3个工作日')
    assert not c['field_evidence']['starting_events']
    assert 'WAITING_START_EVENT_NOT_BOUND' in c['semantic_gaps']


def test_enumeration_keeps_common_premise():
    c=card('对于父母一方亡故另一方再婚后要求变更未成年子女姓名的，公安机关区别情形：\n（一）以本人的劳动收入为主要生活来源的16周岁以上未满18周岁的未成年人，自主决定姓名。','18周岁')
    assert any('父母一方亡故另一方再婚' in r for r in c['qualifiers'])


def test_nested_consent_condition_is_not_global():
    c=card('18周岁以下人员变更姓名的，由其父母双方共同提出书面申请，其中属8周岁以上人员变更姓名的，应征得本人同意。','18周岁')
    assert '8周岁以上' not in c['qualifiers']
    nested=c['field_evidence']['conditional_rules'][0]
    assert nested['scope']=='subrule_only'
    assert nested['conditions'][0]['text']=='8周岁以上'
    assert nested['actions'][0]['text']=='应征得本人同意'


def test_count_object_excludes_document_title():
    c=card('河南公安机关2025年便民利民十二项措施政策解读','十二项')
    assert c['subjects']==['便民利民十二项措施']


def test_information_acquisition_keeps_scan_trigger():
    c=card('群众通过“扫码”可清单式一次性了解窗口相关业务所有办理信息。','一次')
    assert c['field_evidence']['starting_events'][0]['text']=='群众通过“扫码”'
    assert c['quantity_interpretation']=='information_bundle'


def test_v3_remains_frozen_and_wrong_source_versions_fail():
    import extract_fact_cards_v3 as v3
    u=unit('段1','18周岁以下人员变更姓名，其中属8周岁以上人员，应征得本人同意。')
    assert v3.extract([u])[0]['extractor']==v3.VERSION
    with pytest.raises(ValueError,match='AMBIGUOUS_SOURCE_VERSION_OR_UNIT'):
        v4.extract([u,{**u,'text':'different'}])

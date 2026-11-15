import extract_fact_cards_v4_1 as candidate
from test_extract_fact_cards import unit


def test_main_application_action_and_object_exclude_other_age_rules():
    u=unit('段1','18周岁以下人员变更姓名的，由其父母双方共同提出书面申请，其中属8周岁以上人员变更姓名的，应征得本人同意。\n18周岁以上人员变更姓名的，由本人提出书面申请。')
    cards=candidate.extract([u])
    c=cards[0];f=c['field_evidence']
    assert [r['text'] for r in f['actions']]==['由其父母双方共同提出书面申请']
    assert len(f['objects'])==1 and f['objects'][0]['span'][1] < u['text'].index('其中')
    assert f['conditional_rules'][0]['actions'][0]['text']=='应征得本人同意'
    assert '8周岁以上' not in c['qualifiers']
    adult=next(c for c in cards if c['span'][0]>u['text'].index('\n'))
    assert 'main_rule' not in adult['field_evidence']
    assert candidate.verify_field_references(cards,[u])==[]
    assert all(not c['formal_approval'] and c['review_state']=='machine_candidate_unreviewed' for c in cards)


def test_no_main_action_is_fabricated_when_unstated():
    c=candidate.extract([unit('段1','18周岁以下人员变更姓名，其中属8周岁以上人员变更姓名的，应征得本人同意。')])[0]
    assert 'main_rule' not in c['field_evidence']
    assert not c['field_evidence']['actions']


def test_rule_is_not_keyed_to_sample_age_or_source_id():
    c=candidate.extract([unit('段9','16周岁以下人员变更姓名的，由其父母双方共同提出书面申请。')])[0]
    assert c['field_evidence']['main_rule']['conditions'][0]['text']=='16周岁以下'

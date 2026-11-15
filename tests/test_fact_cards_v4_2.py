import extract_fact_cards_v4_2 as candidate
from test_extract_fact_cards import unit


def test_consent_preserves_guardian_agreement_and_request_condition():
    u=unit('段1','8周岁以上的未成年人的父亲和继母，或者母亲和继父经协商同意，要求变更该未成年人姓名的，应当征得其本人的同意。')
    cards=candidate.extract([u]);c=cards[0]
    assert any('经协商同意，要求变更该未成年人姓名的' in r for r in c['qualifiers'])
    assert c['field_evidence']['actions'][0]['text']=='应当征得其本人的同意'
    assert not candidate.verify_field_references(cards,[u])


def test_below_age_and_joint_decision_are_preserved():
    u=unit('段1','不满7周岁的未成年人姓名的变更，由其父亲和继母，或者母亲和继父协商一致后决定。')
    c=candidate.extract([u])[0]
    assert '不满7周岁' in c['qualifiers']
    assert c['field_evidence']['actions'][0]['text']=='由其父亲和继母，或者母亲和继父协商一致后决定'
    assert 'APPLICABILITY_NOT_ESTABLISHED' not in c['semantic_gaps']


def test_two_same_duration_occurrences_have_different_actions():
    u=unit('段1','群众在派出所申领、补领、换领居民身份证后，公安机关在23日内完成居民身份证的审核、签发、制作，并发放至派出所，确保群众可在23日内领取证件。')
    cards=[c for c in candidate.extract([u]) if c['type']=='duration']
    assert len(cards)==2
    assert any('审核、签发' in r['text'] for r in cards[0]['field_evidence']['actions'])
    assert [r['text'] for r in cards[1]['field_evidence']['actions']]==['确保群众可在23日内领取证件']
    assert cards[1]['duration_role']=='collection_promise'
    assert not candidate.verify_field_references(cards,[u])


def test_conditions_and_actions_do_not_cross_sentences_or_appear_unstated():
    u=unit('段1','8周岁以上人员变更姓名。由其父亲和继母，或者母亲和继父协商一致后决定。')
    c=candidate.extract([u])[0]
    assert not c['field_evidence']['actions']
    assert not c['formal_approval'] and c['review_state']=='machine_candidate_unreviewed'

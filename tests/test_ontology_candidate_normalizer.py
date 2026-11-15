from copy import deepcopy
import pytest
from test_extract_fact_cards import unit
from normalize_ontology_candidates import normalize,verify_references


def proposal(kind,subject,predicate,obj,quote,qualifiers=None):
    return dict(kind=kind,subject=subject,predicate=predicate,object=obj,qualifiers=qualifiers or [],quote=quote,quote_in_source=True)


def run(c,text=None):
    u=unit('段1',text or c['quote']);before=deepcopy(c)
    result=normalize(c,[u]);verify_references(result,[u]);assert c==before
    assert not result['formal_approval'] and result['review_state']=='machine_candidate_unreviewed'
    assert result['current_applicability']=='unknown'
    return result


def test_obligation_branch_does_not_exclude_voluntary_application():
    quote='公民应当自年满十五周岁之日起四个月内，申请领取居民身份证。'
    c=run(proposal('condition','公民申领居民身份证','applies_when','年满十五周岁',quote),quote+'未满十五周岁的公民可以自愿申请。')
    assert c['scope']=='stated_obligation_branch_only'
    assert '义务分支' in c['subject']


def test_deadline_keeps_within_instead_of_exact_elapsed_duration():
    c=run(proposal('value','申领期限','为','四个月','自年满十五周岁之日起四个月内'))
    assert c['object']=='四个月内' and c['deadline']['comparison']=='within'
    assert c['field_evidence']['starting_event'][0]['text']=='自年满十五周岁之日起'


@pytest.mark.parametrize('kind,predicate,quote,expected',[
    ('condition','应当','收费标准应当向社会公开。','obligation'),
    ('exception','严禁','严禁超标准收费。','prohibition'),
    ('condition','应严格执行','要严格执行有关文件规定。','obligation'),
    ('condition','收取应依据','应当按照《规范》的规定执行。','legal_basis')])
def test_normative_role_is_not_condition_or_exception(kind,predicate,quote,expected):
    assert run(proposal(kind,'收费','应当' if predicate=='应当' else predicate,'标准',quote))['kind']==expected


def test_collecting_actor_is_not_the_beneficiary_and_missing_actor_stays_unknown():
    quote='对于申请加急办证的居民，可办理临时证，但不得借此收取快证费。'
    c=proposal('exception','申请加急办证的居民','不得收取','快证费',quote)
    known=run(c,'各级公安机关应提高效率。'+quote)
    assert known['subject']=='各级公安机关' and known['beneficiary']=='申请加急办证的居民'
    unknown=run(c)
    assert unknown['subject']=='unknown' and 'ACTOR_NOT_BOUND' in unknown['semantic_gaps']


def test_correction_has_joint_authorities_as_actor_not_charges():
    quote='立即停止并纠正违规收费行为'
    c=run(proposal('exception','各项收费','立即停止并纠正','违规收费行为',quote),
        '各省、自治区、直辖市财政、价格主管部门要按照规定，会同公安部门清理，'+quote+'。')
    assert c['subject']=='各省、自治区、直辖市财政、价格主管部门会同公安部门'
    assert c['kind']=='obligation' and c['subject_label_is_composed']


def test_liability_records_trigger_and_targets_without_inventing_enforcer():
    c=run(proposal('exception','违规收费行为','追究责任','相关负责人',
        '本通知印发后，若再发生批准或继续违规收费的行为，将追究相关负责人的责任'))
    assert c['kind']=='liability'
    assert c['liability_rule']['trigger']=='再发生批准或继续违规收费的行为'
    assert c['liability_rule']['enforcement_actor']=='unknown'


def test_repeal_links_new_document_old_document_and_date_but_does_not_invent_date():
    quote='《旧规范》同时废止。';c=proposal('version_change','《旧规范》','废止','同时废止',quote)
    known=run(c,'本实施规定自2023年2月1日起施行。'+quote)
    assert (known['subject'],known['object'])==('本实施规定','《旧规范》')
    assert known['version_relation']['effective_date']=='2023年2月1日'
    assert 'VERSION_DATE_NOT_BOUND' in run(c)['semantic_gaps']


def test_unstated_condition_and_channel_do_not_become_eligibility_claims():
    c=run(proposal('value','办理时限','取值','20日','办理时限为20日。',['未限定适用对象']))
    assert c['qualifiers']==['本引用未明示对象限定'] and c['applicability_from_quote']=='unknown'
    c=run(proposal('condition','群众换领','无需到','户籍地派出所','群众换领无需到户籍地派出所。'))
    assert c['kind']=='channel'


def test_genuine_exception_is_not_retyped_and_invalid_sources_fail_closed():
    c=proposal('exception','特定申请人','免收','费用','特定申请人免收费用。')
    assert run(c)['kind']=='exception'
    u=unit('段1',c['quote'])
    with pytest.raises(ValueError,match='HASH_MISMATCH'):normalize(c,[{**u,'text':'altered'}])
    with pytest.raises(ValueError,match='AMBIGUOUS_SOURCE_VERSION'):normalize(c,[{**u,'revision':'v1'},{**u,'revision':'v2'}])
    with pytest.raises(ValueError,match='MISSING_OR_AMBIGUOUS'):normalize(c,[unit('段1','无此引文。')])

"""Opt-in offline semantic normalization of source-bound machine candidates.

Not imported by the business ontology engine. No model call, acceptance or store
write. Derived node labels are explicitly distinguished from verbatim evidence.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

VERSION='ontology-candidate-normalizer-v1'
KINDS={'entity','relation','condition','material','step','value','version_change','exception',
       'obligation','prohibition','legal_basis','liability','channel'}


def refs(fragment,units):
    """Bind normalized whitespace to actual source spans; never invent quote text."""
    needle=re.sub(r'\s+','',fragment)
    if not needle:return []
    result=[]
    for u in units:
        text=u['text'];indices=[i for i,ch in enumerate(text) if not ch.isspace()]
        compact=''.join(text[i] for i in indices)
        for m in re.finditer(re.escape(needle),compact):
            a,b=indices[m.start()],indices[m.end()-1]+1
            result.append({'source_id':u['source_id'],'locator':u['locator'],'span':[a,b],
                'text':text[a:b],'unit_text_sha256':sha256(text.encode()).hexdigest()})
    return result


def normalize(candidate,units):
    for u in units:
        if sha256(u['text'].encode()).hexdigest()!=u['text_sha256']:raise ValueError('SOURCE_TEXT_HASH_MISMATCH')
    identities={}
    for u in units:
        identity=(u['source_id'],u['locator'])
        state=(u['text_sha256'],u.get('revision_id'),u.get('revision'),u.get('original_sha256'))
        if identity in identities and identities[identity]!=state:raise ValueError('AMBIGUOUS_SOURCE_VERSION')
        identities[identity]=state
    bound=refs(candidate['quote'],units)
    if len(bound)!=1:raise ValueError('QUOTE_MISSING_OR_AMBIGUOUS')
    anchor=bound[0]
    unit=next(u for u in units if (u['source_id'],u['locator'])==(anchor['source_id'],anchor['locator']))
    quote=candidate['quote'];text=unit['text'];c=deepcopy(candidate)
    evidence={'quote':bound};changes=[];gaps=[]
    def evidence_for(name,fragment):
        found=refs(fragment,[unit])
        if not found:raise ValueError('NORMALIZED_FIELD_NOT_IN_SOURCE:'+name)
        evidence[name]=found
        return found
    if c['kind']=='condition' and c['predicate']=='applies_when' and '应当' in quote and '申请领取' in quote:
        c['subject']=c['subject']+'（本条应当申请领取的义务分支）'
        c['scope']='stated_obligation_branch_only'
        c['subject_label_is_composed']=True
        evidence_for('obligation_context',quote)
        evidence_for('condition',c['object'])
        changes.append('OBLIGATION_BRANCH_SCOPE')
    if c['kind']=='value' and '期限' in c['subject']:
        m=re.search(r'(?P<value>[\d一二三四五六七八九十百]+(?:个)?(?:月|日|工作日|年))内',quote)
        if m and c['object']==m['value']:
            c['object']=m.group();c['deadline']={'comparison':'within','upper_bound':m['value'],
                'scope':'quoted_deadline_only'}
            evidence_for('deadline',m.group())
            start=re.search(r'自[^，,。；;]+之日起',quote)
            if start:evidence_for('starting_event',start.group())
            changes.append('DEADLINE_UPPER_BOUND')
    if c['kind']=='condition' and re.search(r'应当|应严格|应依据',c['predicate']):
        c['kind']='legal_basis' if re.search(r'按照.*规定执行',quote) else 'obligation'
        evidence_for('normative_context',quote);changes.append('NORM_NOT_APPLICABILITY')
    if c['kind']=='exception' and re.search(r'严禁|不得',c['predicate']):
        c['kind']='prohibition';evidence_for('prohibition_context',quote);changes.append('PROHIBITION_NOT_EXCEPTION')
        if '无权' in quote:
            m=re.search(r'无权[^。；;]+',quote)
            c['authority_limit']={'kind':'authority_limit','action':m.group()}
            evidence_for('authority_limit',m.group())
    if c['kind']=='prohibition' and '申请加急办证的居民' in c['subject']:
        # Actor must be explicit in the same source unit, outside the beneficiary.
        actors=list(re.finditer(r'(?:各级)?公安机关',text[:anchor['span'][0]]))
        c['beneficiary']=candidate['subject'];evidence_for('beneficiary',candidate['subject'])
        if len(actors)==1:
            c['subject']=actors[0].group();evidence_for('actor',c['subject'])
        else:c['subject']='unknown';gaps.append('ACTOR_NOT_BOUND')
        changes.append('ACTOR_VS_BENEFICIARY')
    if c['kind']=='exception' and c['predicate']=='立即停止并纠正':
        c['kind']='obligation'
        lead=re.search(r'(?P<authority>各省、自治区、直辖市财政、价格主管部门).*?(?P<joint>会同公安部门)',text)
        if lead:
            c['subject']=lead['authority']+lead['joint'];c['subject_label_is_composed']=True
            evidence_for('lead_actor',lead['authority']);evidence_for('co_actor',lead['joint'])
        else:c['subject']='unknown';gaps.append('ACTOR_NOT_BOUND')
        evidence_for('corrective_action',quote);changes.append('CORRECTIVE_ACTOR_AND_OBLIGATION')
    if c['kind']=='exception' and c['predicate']=='追究责任':
        c['kind']='liability';c['predicate']='触发对责任对象的追责'
        trigger=re.search(r'若(?P<trigger>[^，,。；;]+)',quote)
        consequence=re.search(r'追究[^。；;]+的责任',quote)
        if trigger and consequence:
            c['liability_rule']={'trigger':trigger['trigger'],'consequence':consequence.group(),
                'responsible_persons':c['object'],'enforcement_actor':'unknown'}
            evidence_for('liability_trigger',trigger['trigger']);evidence_for('liability_consequence',consequence.group())
            evidence_for('responsible_persons',c['object'])
        else:gaps.append('LIABILITY_BINDING_INCOMPLETE')
        changes.append('LIABILITY_NOT_EXCEPTION')
    if c['kind']=='version_change' and c['predicate']=='废止' and c['object']=='同时废止':
        old_title=c['subject']
        effective=re.search(r'(?P<new>本实施规定)自(?P<date>\d{4}年\d{1,2}月\d{1,2}日)起施行',text)
        if effective and old_title in quote and '同时废止' in quote:
            c.update(subject=effective['new'],predicate='施行时同时废止',object=old_title)
            c['version_relation']={'new_document':effective['new'],'old_document':old_title,
                'event':'simultaneous_repeal_on_commencement','effective_date':effective['date'],
                'current_validity':'unknown'}
            evidence_for('commencement',effective.group());evidence_for('old_document',old_title)
            evidence_for('simultaneous_repeal',quote);changes.append('EXPLICIT_VERSION_RELATION')
        else:gaps.append('VERSION_DATE_NOT_BOUND')
    if '未限定适用对象' in c['qualifiers']:
        c['qualifiers']=['本引用未明示对象限定' if q=='未限定适用对象' else q for q in c['qualifiers']]
        c['applicability_from_quote']='unknown'
        evidence_for('applicability_scope',quote);changes.append('UNSTATED_NOT_UNRESTRICTED')
    if c['kind']=='condition' and c['predicate']=='无需到' and '派出所' in c['object']:
        c['kind']='channel';evidence_for('channel_rule',quote);changes.append('CHANNEL_NOT_APPLICABILITY')
    if c['kind'] not in KINDS:raise ValueError('UNSUPPORTED_NORMALIZED_KIND')
    c.update(normalizer=VERSION,field_evidence=evidence,normalization_changes=changes,semantic_gaps=gaps,
        review_state='machine_candidate_unreviewed',formal_approval=False,current_applicability='unknown')
    c['candidate_id']=sha256(json.dumps({'version':VERSION,'before':candidate,'after':c},
        ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return c


def verify_references(candidate,units):
    by_unit={(u['source_id'],u['locator']):u for u in units}
    for group in candidate['field_evidence'].values():
        for r in group:
            u=by_unit[r['source_id'],r['locator']];a,b=r['span']
            if not 0<=a<b<=len(u['text']) or u['text'][a:b]!=r['text'] or u['text_sha256']!=r['unit_text_sha256']:
                raise ValueError('NORMALIZED_REFERENCE_MISMATCH')

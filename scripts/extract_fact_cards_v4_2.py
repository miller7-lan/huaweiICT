"""Opt-in age-direction, consent and collection-action candidates, offline only."""
from hashlib import sha256
import json
import re
import extract_fact_cards_v4_1 as previous

VERSION='fact-cards-v4.2-rule-action-candidate'


def extract(units,sources=None):
    cards=previous.extract(units,sources)
    by_unit={(u['source_id'],u['locator']):u for u in units}
    for c in cards:
        u=by_unit[c['source_id'],c['locator']];text=u['text'];start,end=c['span']
        base=previous.v4;left,right=base.v3.v1.around(text,base.v3.v1.sentences(text),start,end)
        f=c['field_evidence']
        if c.get('quantity_interpretation')=='age':
            # Preserve direction: "not yet N years" is not a bare age value.
            ages=base.matches(r'不满\s*'+base.v3.NUMBER+r'\s*周岁',u,left,right)
            bound=[r for r in ages if r['span'][0]<=start and end<=r['span'][1]]
            for r in bound:
                if r not in f['conditions']:f['conditions'].append(r)
            agreements=base.matches(r'(?:其)?父亲和继母，或者母亲和继父经协商同意，要求变更[^。；;\n]+?姓名的',u,left,right)
            consents=base.matches(r'(?:应当|必须|应)征得其?本人(?:的)?同意',u,left,right)
            decisions=base.matches(r'由其父亲和继母，或者母亲和继父协商一致后决定',u,left,right)
            if agreements and consents:
                f['conditions'].extend(agreements)
                f['actions'].extend(consents)
                f['consent_rule']={'context':base.v3.ref(u,left,right),'conditions':agreements,'actions':consents}
            if bound and decisions:
                f['actions'].extend(decisions)
                f['decision_rule']={'context':base.v3.ref(u,left,right),'conditions':bound,'actions':decisions}
        # Match the occurrence carrying the card's number, not a different 25-day
        # occurrence describing a different actor and completion stage.
        if c['type']=='duration':
            collections=base.matches(r'确保群众可在'+base.v3.QUANTITY+r'内领取证件',u,left,right)
            bound=[r for r in collections if r['span'][0]<=start and end<=r['span'][1]]
            if bound:
                f['actions']=bound
                f['action_clause']=bound[0]
                c['duration_role']='collection_promise'
        c['qualifiers']=list(dict.fromkeys(r['text'] for r in f['conditions']))
        if any('不满' in r['text'] for r in f['conditions']):
            c['semantic_gaps']=[g for g in c['semantic_gaps'] if g!='APPLICABILITY_NOT_ESTABLISHED']
        c['extractor']=VERSION
        c['semantic_id']=sha256(json.dumps({'version':VERSION,'previous_semantic_id':c['semantic_id'],
            'fields':f,'duration_role':c.get('duration_role')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return cards


verify_field_references=previous.verify_field_references

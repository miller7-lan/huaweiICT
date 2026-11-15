"""Opt-in main-rule action/scope repair; V4 reviewed evidence remains frozen."""
from hashlib import sha256
import json
import re

import extract_fact_cards_v4 as v4

VERSION = 'fact-cards-v4.1-main-rule-candidate'


def extract(units, sources=None):
    cards = v4.extract(units, sources)
    by_unit = {(u['source_id'],u['locator']):u for u in units}
    for card in cards:
        unit = by_unit[card['source_id'],card['locator']]
        text = unit['text']
        left,right = v4.v3.v1.around(text,v4.v3.v1.sentences(text),*card['span'])
        # Limit the main rule to its own sentence and the part preceding "其中".
        marker = text.find('其中',left,right)
        main_end = marker if marker >= 0 else right
        actions = v4.matches(r'由其父母双方共同提出书面申请',unit,left,main_end)
        ages = v4.matches(v4.v3.NUMBER+r'\s*周岁以下',unit,left,main_end)
        if actions and ages:
            action = actions[0]
            objects = v4.matches(r'(?:姓名(?:的)?变更|变更姓名)',unit,left,action['span'][0])
            if objects:
                fields = card['field_evidence']
                fields['objects'] = objects
                # Subrule consent actions stay inside conditional_rules.
                fields['actions'] = actions
                fields['main_rule'] = {'context':v4.v3.ref(unit,left,main_end),
                    'conditions':ages,'objects':objects,'actions':actions}
                card['subjects'] = list(dict.fromkeys(r['text'] for r in objects))
        card['extractor'] = VERSION
        card['semantic_id'] = sha256(json.dumps({'version':VERSION,
            'previous_semantic_id':card['semantic_id'],'fields':card['field_evidence']},
            sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return cards


verify_field_references = v4.verify_field_references

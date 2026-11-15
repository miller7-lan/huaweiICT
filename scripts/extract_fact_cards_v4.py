"""Opt-in offline semantic repairs; frozen V3 and human decisions stay unchanged.

Exact text references are candidates, not proof of semantic entailment.
"""
from hashlib import sha256
import json
import re

import extract_fact_cards_v3 as v3

VERSION = 'fact-cards-v4-clause-candidate'


def matches(pattern, unit, left, right):
    return v3.references(re.compile(pattern), unit, left, right)


def repair(card, unit):
    text = unit['text']
    start, end = card['span']
    left, right = v3.v1.around(text, v3.v1.sentences(text), start, end)
    fields = card['field_evidence']
    # Fees with distinct conditions must bind to the local charging clause.
    if card['type'] == 'amount' and 'table_columns' not in fields:
        a = max(text.rfind(mark, left, start) + len(mark)
                for mark in ('；', ';', '即'))
        a = max(left, a)
        b = min([right] + [p for mark in ('；', ';')
                          if (p := text.find(mark, end, right)) >= 0])
        subject = list(re.finditer(r'向(?P<object>[^；;。]+?)的居民收取|为居民办理(?P<service>[^；;。]+?)收取', text[a:b]))
        subject = [m for m in subject if a + m.end() <= start]
        if subject:
            m = subject[-1]
            group = 'object' if m.group('object') is not None else 'service'
            fields['objects'] = [v3.ref(unit, a+m.start(group), a+m.end(group))]
            fields['object_clause'] = v3.ref(unit, a, b)
    # Extract actions themselves; the application event is a separate field.
    trigger = '群众在派出所申领、补领、换领居民身份证后'
    fields['actions'] = [r for r in fields['actions'] if r['text'] != trigger]
    fields['actions'].extend(matches(
        r'公安机关在' + v3.QUANTITY + r'内完成[^（(。；;\n]+|'
        r'[^，,。；;\n]{0,25}对居民身份证制证流程再次优化完善', unit, left, right))
    # Waiting duration and its endpoint must not masquerade as the start event.
    endpoints = matches(r'告知\s*' + v3.QUANTITY + r'后', unit, left, right)
    if endpoints:
        endpoint_keys = {(r['locator'], tuple(r['span'])) for r in endpoints}
        for key in ('starting_events', 'conditions'):
            fields[key] = [r for r in fields[key]
                           if (r['locator'], tuple(r['span'])) not in endpoint_keys]
        # The explicit written notice may occur in the previous semicolon clause.
        notices = matches(r'书面告知[^，,。；;\n]+', unit, 0, start)
        if notices:
            fields['starting_events'].append(notices[-1])
        else:
            card['semantic_gaps'].append('WAITING_START_EVENT_NOT_BOUND')
        fields['waiting_endpoints'] = endpoints
    # A lead-in before enumerated subrules is a common premise, not an age rule.
    premises = matches(r'对于[^。；;\n：:]+要求变更[^。；;\n：:]+的', unit, 0, start)
    if premises and re.search(r'[：:]\s*[（(]一[）)]', text[premises[-1]['span'][1]:start]):
        fields['conditions'].append(premises[-1])
        fields['rule_premises'] = [premises[-1]]
    # Subconditions introduced by "其中" apply to their own action, not every rule.
    nested = list(re.finditer(r'其中[^。；;\n]+', text[left:right]))
    if nested:
        relations = []
        removed = set()
        for match in nested:
            a, b = left+match.start(), left+match.end()
            subconditions = matches(v3.CONDITION.pattern, unit, a, b)
            actions = matches(r'(?:应当|应|必须)征得[^。；;\n]+', unit, a, b)
            if subconditions and actions:
                relations.append({'scope': 'subrule_only', 'context': v3.ref(unit,a,b),
                                  'conditions': subconditions, 'actions': actions})
                removed.update(tuple(r['span']) for r in subconditions)
        if relations:
            fields['conditional_rules'] = relations
            fields['conditions'] = [r for r in fields['conditions'] if tuple(r['span']) not in removed]
    # Counted measures are narrower than the containing policy title.
    if card['type'] == 'count' and card['value']['unit'] == '项':
        measures = matches(r'便民利民\s*' + v3.NUMBER + r'项措施', unit, left, right)
        containing = [r for r in measures if r['span'][0] <= start and end <= r['span'][1]]
        if containing:
            fields['objects'] = containing
    if any('一次性了解' in r['text'] for r in fields['actions']):
        fields['starting_events'].extend(matches(r'群众通过[“"「]?扫码[”"」]?', unit, left, right))
    # Canonical deduplication preserves source/version-bound references.
    for key, value in fields.items():
        if isinstance(value, list):
            seen = set(); unique = []
            for item in value:
                token = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if token not in seen:seen.add(token); unique.append(item)
            fields[key] = unique
    card['subjects'] = list(dict.fromkeys(r['text'] for r in fields['objects']))
    card['qualifiers'] = list(dict.fromkeys(r['text'] for r in fields['conditions']))
    card['extractor'] = VERSION
    card['semantic_id'] = sha256(json.dumps({'version': VERSION, 'previous_semantic_id': card['semantic_id'],
        'fields': fields}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return card


def extract(units, sources=None):
    # Reuse all V3 source/version ambiguity, approval and provenance protections.
    cards = v3.extract(units, sources)
    by_unit = {(u['source_id'],u['locator']):u for u in units}
    return [repair(c, by_unit[c['source_id'],c['locator']]) for c in cards]


verify_field_references = v3.verify_field_references

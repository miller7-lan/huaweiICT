"""One offline development comparison; refuses to overwrite a previous run."""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import time

import extract_fact_cards_v4 as v4
from review_fact_cards_v3 import display

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'docs/competition/dual-track-improvement-202611'
OLD=ROOT/'.runtime/dual-track-improvement-202611/offline-finish-v3-r2'
FOCUS=(1,6,8,11,13,17,20,37)


def digest(p):return sha256(Path(p).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('EXISTING_RUN_PRESERVED')
    manifest=json.loads((REPORT/'human-review-v3-manifest.json').read_text('utf-8'))
    assert digest(args.corpus)==manifest['source_corpus_sha256']
    assert digest(OLD/'cards-v3.jsonl')==manifest['source_candidate_sha256']
    assert digest(OLD/'sample-comparison-63.json')==manifest['source_comparison_sha256']
    protected={p:digest(REPORT/p) for p in ('human-review.csv','human-review-summary.json',
        'human-review-v3.csv','human-review-v3-summary.json','ocr-human-review.csv','ocr-human-review-summary.json')}
    corpus=json.loads(args.corpus.read_text('utf-8'))
    before=[json.loads(line) for line in (OLD/'cards-v3.jsonl').read_text('utf-8').splitlines()]
    started=time.perf_counter();after=v4.extract(corpus['units'],corpus['manifest']['sources']);elapsed=time.perf_counter()-started
    assert not v4.verify_field_references(after,corpus['units'])
    old={c['card_id']:c for c in before};new={c['card_id']:c for c in after}
    assert set(old)==set(new)
    # Values, types and table coordinates are outside this semantic change.
    for key,c in new.items():
        assert all(c[f]==old[key][f] for f in ('type','value','source_id','locator','span','text'))
        assert c['formal_approval'] is False and c['review_state']=='machine_candidate_unreviewed'
    changed=[key for key in old if old[key]['field_evidence']!=new[key]['field_evidence']]
    pairs=json.loads((OLD/'sample-comparison-63.json').read_text('utf-8'))
    comparison=[{'number':p['number'],'before':p['after'],
        'after':new[p['after']['card_id']] if p['after'] else None,'new_human_decision':None} for p in pairs]
    changed_sample=[p['number'] for p in comparison if p['before'] and
                    p['before']['field_evidence']!=p['after']['field_evidence']]
    args.output.mkdir(parents=True)
    def write(name,value):
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8')
    (args.output/'cards-v4.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in after),'utf-8')
    write('sample-comparison-63.json',comparison)
    write('changed-card-ids.json',changed)
    units={(u['source_id'],u['locator']):u for u in corpus['units']}
    blocks=['# V4首批开发者人工复核：8项\n\n'
        '本批共8项。新版机器候选均未评分；逐项选正确/部分正确/错误/无法判断并附理由。'
        '这是开发样本，不是独立留出；精确引用不保证语义绑定。V3及旧审核保持。\n\n'
        '[原件与Excel/HTML/PDF补件](../source-supplement/README.md)\n\n']
    for p in comparison:
        if p['number'] not in FOCUS:continue
        c=p['after'];refs={(c['source_id'],c['locator'])}
        def collect(value):
            if isinstance(value,dict):
                if {'source_id','locator','span','text'}<=value.keys():refs.add((value['source_id'],value['locator']))
                for child in value.values():collect(child)
            elif isinstance(value,list):
                for child in value:collect(child)
        collect(c['field_evidence'])
        blocks.append(f"## {p['number']} · {c['source_id']} · {c['locator']}\n\n"
            +f"值/类型：`{json.dumps(c['value'],ensure_ascii=False)}` / `{c['type']}`\n\n"
            +'### 新版全部字段引用\n\n'+display(c['field_evidence'])
            +'### 尚未建立字段\n\n'+json.dumps(c['semantic_gaps'],ensure_ascii=False)+'\n\n')
        for key in sorted(refs):
            u=units[key];blocks.append(f"### 完整来源：{key[0]} · {key[1]}\n\n```text\n{u['text']}\n```\n\n")
        blocks.append('来源身份：\n\n```json\n'+json.dumps(c['provenance'],ensure_ascii=False,indent=2)
                      +'\n```\n\n本人判定：____；理由：____\n\n')
    (args.output/'human-review-eight.md').write_text(''.join(blocks),'utf-8')
    with (args.output/'human-review-eight.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['number','semantic_id','review_kind','reviewer','decision','reason'])
        writer.writeheader();writer.writerows({'number':p['number'],'semantic_id':p['after']['semantic_id'],
            'review_kind':'开发者人工审核','reviewer':'','decision':'','reason':''}
            for p in comparison if p['number'] in FOCUS)
    for p,h in protected.items():assert digest(REPORT/p)==h
    result={'label':'offline_development_comparison','unit_count':len(corpus['units']),
        'before_count':len(before),'after_count':len(after),'changed_semantic_cards':len(changed),
        'changed_sample_numbers':changed_sample,'focus_numbers':list(FOCUS),'human_review_completed':0,
        'field_reference_errors':0,'same_card_ids_values_types_spans':True,
        'extraction_elapsed_seconds':round(elapsed,6),'before_extraction_elapsed_seconds':None,
        'new_api_calls':0,'model_tokens':0,'model_cost_usd':0,'business_state_changes':0,
        'corpus_sha256':digest(args.corpus),'before_cards_sha256':digest(OLD/'cards-v3.jsonl'),
        'after_cards_sha256':digest(args.output/'cards-v4.jsonl'),
        'extractor_sha256':digest(ROOT/'scripts/extract_fact_cards_v4.py'),
        'baseline_extractor_sha256':digest(ROOT/'scripts/extract_fact_cards_v3.py'),
        'protected_evidence_sha256':protected,
        'note':'同输入离线开发对比；规则检查和精确字符匹配不等于业务正确，未形成新版人工成绩或独立效果。'}
    write('receipt.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

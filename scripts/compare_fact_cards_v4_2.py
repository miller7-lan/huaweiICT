"""Reproducible offline V4.1/V4.2 comparison and blank review packet."""
import argparse,csv,json,time
from pathlib import Path
from hashlib import sha256
import extract_fact_cards_v4_2 as candidate
from review_fact_cards_v3 import display

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'docs/competition/dual-track-improvement-202611'
OLD=ROOT/'.runtime/dual-track-improvement-202611/fact-cards-v4_1-r1'


def sha(p):return sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('EXISTING_RUN_PRESERVED')
    receipt=json.loads((OLD/'receipt.json').read_text('utf-8'))
    assert sha(a.corpus)==receipt['corpus_sha256']
    assert sha(OLD/'cards-v4_1.jsonl')==receipt['after_cards_sha256']
    protected={str(f):sha(f) for f in REPORT.glob('human-review*.csv')}
    protected.update({str(f):sha(f) for f in OLD.iterdir() if f.is_file()})
    corpus=json.loads(a.corpus.read_text('utf-8'))
    before={c['card_id']:c for c in map(json.loads,(OLD/'cards-v4_1.jsonl').read_text('utf-8').splitlines())}
    t=time.perf_counter();cards=candidate.extract(corpus['units'],corpus['manifest']['sources']);elapsed=time.perf_counter()-t
    after={c['card_id']:c for c in cards};assert set(before)==set(after)
    assert not candidate.verify_field_references(cards,corpus['units'])
    for key,c in after.items():
        assert all(c[f]==before[key][f] for f in ('type','value','span','source_id','locator'))
        assert not c['formal_approval'] and c['review_state']=='machine_candidate_unreviewed'
    changed=[key for key in after if after[key]['field_evidence']!=before[key]['field_evidence'] or after[key].get('duration_role')!=before[key].get('duration_role')]
    with (REPORT/'human-review-v4.csv').open(encoding='utf-8-sig',newline='') as f:labels={r['card_id']:r['item'] for r in csv.DictReader(f)}
    assert len(changed)<=10
    units={(u['source_id'],u['locator']):u for u in corpus['units']}
    blocks=['# V4.2完整开发者人工复核材料\n\n只评本版本，旧版本和旧失败保留；新版尚未评分。'
        '每项回复正确/部分正确/错误/无法判断及理由。\n\n[归档原件与结构补件](../source-supplement/README.md)\n\n']
    for index,key in enumerate(changed,1):
        c=after[key];u=units[c['source_id'],c['locator']]
        label=labels.get(key,'新增关联项'+str(index))
        blocks.append(f"## {label} · {key}\n\n{c['source_id']} · {c['locator']}\n\n"
            +'值/类型/位置：`'+json.dumps({f:c[f] for f in ('value','type','span')},ensure_ascii=False)+'`\n\n'
            +'时长角色：`'+str(c.get('duration_role','不适用'))+'`\n\n'
            +'### 全部语义字段引用\n\n'+display(c['field_evidence'])
            +'### 尚未建立字段\n\n'+json.dumps(c['semantic_gaps'],ensure_ascii=False)+'\n\n'
            +'### 完整来源单元\n\n```text\n'+u['text']+'\n```\n\n'
            +'### 完整新旧机器卡片\n\n```json\n'+json.dumps({'before':before[key],'after':c},ensure_ascii=False,indent=2)+'\n```\n\n'
            +'本人判定：____；理由：____\n\n')
    a.output.mkdir(parents=True)
    (a.output/'cards-v4_2.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cards),'utf-8')
    (a.output/'human-review-complete.md').write_text(''.join(blocks),'utf-8')
    with (a.output/'human-review.csv').open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['item','card_id','semantic_id','review_kind','reviewer','decision','reason']);w.writeheader()
        w.writerows({'item':labels.get(key,'新增关联项'+str(i)),'card_id':key,'semantic_id':after[key]['semantic_id'],
            'review_kind':'开发者人工审核','reviewer':'','decision':'','reason':''} for i,key in enumerate(changed,1))
    for path,h in protected.items():assert sha(Path(path))==h,path
    result={'label':'offline_development_comparison','before_version':'V4.1','after_version':'V4.2','unit_count':len(corpus['units']),
        'card_count':len(cards),'changed_semantic_cards':len(changed),'changed_items':[labels.get(key,key) for key in changed],
        'unchanged_values_types_positions':True,'field_reference_errors':0,'human_review_completed':0,'extraction_seconds':round(elapsed,6),
        'new_api_calls':0,'model_tokens':0,'model_cost_usd':0,'production_changes':0,'corpus_sha256':sha(a.corpus),
        'baseline_cards_sha256':sha(OLD/'cards-v4_1.jsonl'),'candidate_cards_sha256':sha(a.output/'cards-v4_2.jsonl'),
        'extractor_sha256':sha(ROOT/'scripts/extract_fact_cards_v4_2.py'),'review_material_sha256':sha(a.output/'human-review-complete.md'),
        'protected_evidence_unchanged':len(protected)}
    (a.output/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Prepare readable frozen V3 review material and count the user's real choices.

No extraction, model call, inferred score, or modification of previous evidence.
Filtered item 14 is reviewed as a filtering decision, never counted as a new card.
"""
import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'docs/competition/dual-track-improvement-202611'
INPUT=ROOT/'.runtime/dual-track-improvement-202611/offline-finish-v3-r2'
OUTPUT=ROOT/'.runtime/dual-track-improvement-202611/v3-human-review'
CHOICES=('正确','部分正确','错误','无法判断')
FIELDS=['number','batch','old_card_id','candidate_semantic_id','item_kind','review_kind','reviewer','decision','recorded_at','reason']
LABELS={'objects':'对象/事项候选','conditions':'适用条件候选','modalities':'规范情态（不当作适用条件）',
        'actions':'动作候选','exceptions':'例外候选','frequency':'频次范围','starting_events':'起算/触发事件候选',
        'time_context':'时间上下文（不是现行有效性结论）','changes':'旧新数值关系候选','table_columns':'原表列对照',
        'containing_cells':'当前片段所在单元格'}


def file_sha(path):return sha256(Path(path).read_bytes()).hexdigest()


def display(value,depth=0):
    prefix='  '*depth
    if isinstance(value,dict) and {'source_id','locator','span','text'}<=set(value):
        cell=f"；格{value['cell']}" if 'cell' in value else ''
        return f"{prefix}- {value['text']}\n\n{prefix}  依据：{value['source_id']} · {value['locator']} · 字符{value['span']}{cell}\n\n"
    if isinstance(value,list):
        return ''.join(display(v,depth) for v in value) if value else prefix+'- 未抽取；不代表原文没有相关信息。\n\n'
    if isinstance(value,dict):
        return ''.join(prefix+f'- {key}：\n\n'+display(child,depth+1) for key,child in value.items())
    return prefix+f'- {value}\n\n'


def statistics(rows,manifest):
    expected={item['number']:item for item in manifest['items']}
    if len(rows)!=63 or len({r['number'] for r in rows})!=63 or {r['number'] for r in rows}!=set(expected):
        raise ValueError('REVIEW_IDS_MISSING_DUPLICATED_OR_FOREIGN')
    counts={'retained_candidate':Counter(),'filtered_candidate':Counter()}
    for row in rows:
        item=expected[row['number']]
        for key in ('batch','old_card_id','candidate_semantic_id','item_kind','review_kind'):
            if row[key]!=item[key]:raise ValueError('FROZEN_REVIEW_METADATA_CHANGED')
        if not row['decision']:
            if any(row[key] for key in ('reviewer','recorded_at','reason')):
                raise ValueError('PENDING_ROW_HAS_REVIEW_CONTENT')
            counts[row['item_kind']]['pending']+=1
            continue
        if row['decision'] not in CHOICES or row['reviewer']!='开发者本人' or not row['recorded_at'].strip():
            raise ValueError('ACTUAL_DEVELOPER_DECISION_AND_TIMESTAMP_REQUIRED')
        if row['decision']!='正确' and not row['reason'].strip():
            raise ValueError('NON_CORRECT_DECISION_REQUIRES_REASON')
        counts[row['item_kind']][row['decision']]+=1
    results={}
    for kind,counter in counts.items():
        completed=sum(counter[c] for c in CHOICES)
        results[kind]={'completed':completed,'pending':counter['pending'],'counts':{c:counter[c] for c in CHOICES}}
    completed=sum(x['completed'] for x in results.values())
    card_count=results['retained_candidate']['completed']
    return {'review_kind':'开发者人工审核','version':'fact-cards-v3-semantic-candidate','total_review_tasks':63,
            'completed':completed,'pending':63-completed,'retained_candidates':62,'filtered_decisions':1,
            'by_item_kind':results,
            'strict_correct_over_reviewed_retained':counts['retained_candidate']['正确']/card_count if card_count else None,
            'note':'第14项单列过滤合理性，不并入新卡片精确率。不是独立留出、字符正确率或最终问答效果。'}


def prepare(corpus_path):
    ledger=REPORT/'human-review-v3.csv';manifest_path=REPORT/'human-review-v3-manifest.json'
    summary_path=REPORT/'human-review-v3-summary.json'
    if OUTPUT.exists() or any(p.exists() for p in (ledger,manifest_path,summary_path)):
        raise ValueError('EXISTING_REVIEW_PRESERVED')
    receipt=json.loads((REPORT/'offline-finish-v3-r2.json').read_text('utf-8'))
    assert file_sha(INPUT/'cards-v3.jsonl')==receipt['candidate_cards_file_sha256']
    for name,expected in receipt['protected_evidence_sha256'].items():assert file_sha(ROOT/name)==expected
    pairs=json.loads((INPUT/'sample-comparison-63.json').read_text('utf-8'))
    corpus=json.loads(Path(corpus_path).read_text('utf-8'))
    plan=json.loads((INPUT/'residence-skill/plan.json').read_text('utf-8'))
    assert file_sha(corpus_path)==plan['corpus_file_sha256']
    units={(u['source_id'],u['locator']):u for u in corpus['units']}
    assert len(pairs)==63 and [p['number'] for p in pairs]==list(range(1,64))
    assert [p['number'] for p in pairs if p['after'] is None]==[14]
    OUTPUT.mkdir()
    header=('# 新版V3开发者人工审核：易读版\n\n'
            '只核新版候选的值、类型、事项/对象、条件、日期角色、旧新关系和引用绑定是否正确；'
            '不判断当前法律效力或个人资格。旧评分不展示、不迁移。每项选正确/部分正确/错误/无法判断，非正确附理由。\n\n'
            '第14项已被过滤，请判断过滤是否合理，单独统计；其他62项才是新候选卡片。'
            '候选对象非空、精确片段存在不代表语义正确；规范情态不是适用条件；'
            '“未抽取”不是“没有该条件”。所有字符区间均为对应原单元的零起点、右端不包含。\n\n'
            '[原始新版完整JSON对照](../offline-finish-v3-r2/human-review-v3-all-63.md) · '
            '[归档原件、Excel表头/坐标和HTML元数据补件](../source-supplement/README.md)\n\n')
    grouped={};items=[];rows=[]
    for pair in pairs:
        n=pair['number'];old=pair['before'];new=pair['after'];batch=f'V3-{(n-1)//9+1:02d}'
        item={'number':str(n),'batch':batch,'old_card_id':old['card_id'],
              'candidate_semantic_id':new['semantic_id'] if new else '',
              'item_kind':'retained_candidate' if new else 'filtered_candidate','review_kind':'开发者人工审核'}
        items.append(item);rows.append({**item,'reviewer':'','decision':'','recorded_at':'','reason':''})
        block=f"## {n}. {old['source_id']} · {old['locator']}\n\n"
        block+='原值/类型：`'+json.dumps({'type':old['type'],'value':old['value']},ensure_ascii=False)+'`\n\n'
        if new:
            block+='新版值/类型：`'+json.dumps({'type':new['type'],'value':new['value']},ensure_ascii=False)+'`\n\n'
            block+=f"新版对象摘要：{'、'.join(new['subjects']) or '未建立'}\n\n"
            for key in ('quantity_interpretation','duration_role','year_role'):
                if key in new:block+=f"{key}：`{new[key]}`\n\n"
            block+='尚未建立的字段标记：`'+json.dumps(new['semantic_gaps'],ensure_ascii=False)+'`（空列表不代表人工已核验完整）。\n\n'
            block+='### 每个字段的全部原文引用\n\n'
            for key,value in new['field_evidence'].items():
                block+='#### '+LABELS.get(key,key)+'\n\n'+display(value)
        else:
            block+='新版动作：**过滤，不生成卡片**。请核对原文后判断过滤是否合理；这项不按“产生一张正确新卡片”计入精确率。\n\n'
        referenced={(old['source_id'],old['locator'])}
        def collect(value):
            if isinstance(value,dict):
                if {'source_id','locator','span','text'}<=set(value):referenced.add((value['source_id'],value['locator']))
                for child in value.values():collect(child)
            elif isinstance(value,list):
                for child in value:collect(child)
        if new:collect(new['field_evidence'])
        block+='### 完整来源单元（包括被引用的父项目、标题或其他上下文）\n\n'
        for key in sorted(referenced):
            u=units[key];block+=f"**{key[0]} · {key[1]} · {u.get('evidence_kind')}**\n\n"
            block+=f"单元SHA-256：`{u['text_sha256']}`\n\n```text\n{u['text']}\n```\n\n"
            if u.get('cells'):block+='原单元cells：`'+json.dumps(u['cells'],ensure_ascii=False)+'`。原表结构见前述补件，不能把展开文本直接当成已核验坐标。\n\n'
        if new:block+='原件来源与修订：\n\n```json\n'+json.dumps(new['provenance'],ensure_ascii=False,indent=2)+'\n```\n\n'
        block+='本人判定：____；理由：____\n\n'
        grouped.setdefault(batch,[]).append(block)
    for batch,blocks in grouped.items():(OUTPUT/(batch+'.md')).write_text(header+''.join(blocks),'utf-8')
    (OUTPUT/'all-63-readable.md').write_text(header+''.join(block for blocks in grouped.values() for block in blocks),'utf-8')
    with ledger.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    manifest={'version':'fact-cards-v3-semantic-candidate','review_kind':'开发者人工审核','items':items,
              'source_candidate_sha256':file_sha(INPUT/'cards-v3.jsonl'),
              'source_comparison_sha256':file_sha(INPUT/'sample-comparison-63.json'),
              'source_corpus_sha256':file_sha(corpus_path),
              'material_sha256':{p.name:file_sha(p) for p in sorted(OUTPUT.glob('*.md'))},
              'old_evidence_unchanged':True,'new_api_calls':0,'new_human_scores':0}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n','utf-8')
    result=statistics(rows,manifest)
    summary_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    for name,expected in receipt['protected_evidence_sha256'].items():assert file_sha(ROOT/name)==expected
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','score'))
    parser.add_argument('--corpus',type=Path)
    args=parser.parse_args()
    if args.action=='prepare':
        if args.corpus is None:parser.error('--corpus is required for preparation')
        result=prepare(args.corpus)
    else:
        with (REPORT/'human-review-v3.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        result=statistics(rows,json.loads((REPORT/'human-review-v3-manifest.json').read_text('utf-8')))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

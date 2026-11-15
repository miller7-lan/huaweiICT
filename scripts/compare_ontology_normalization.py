"""Offline paired candidate normalization; immutable inputs and blank review pack."""
import argparse,csv,json,shutil,time
from pathlib import Path
from hashlib import sha256
from zipfile import ZipFile,ZIP_DEFLATED
from generate_unit_candidates import resolve
from normalize_ontology_candidates import normalize,verify_references,VERSION

ROOT=Path(__file__).resolve().parents[1];REPORT=ROOT/'docs/competition/dual-track-improvement-202611'
PRIVATE=ROOT/'.runtime/dual-track-improvement-202611'
CORE=('kind','subject','predicate','object','qualifiers','quote','quote_in_source')


def sha(p):return sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n','utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('candidates','definitions','corpus','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();zpath=a.output.with_suffix('.zip')
    if a.output.exists() or zpath.exists():raise ValueError('EXISTING_OUTPUT_PRESERVED')
    receipt=json.loads((ROOT/'evidence/lowresource-20261105/method-b-candidates-receipt.json').read_text('utf-8'))
    assert sha(a.candidates)==receipt['private_file_sha256'] and sha(a.definitions)==receipt['units_definition_sha256']
    old_data=json.loads(a.candidates.read_text('utf-8'));definitions=json.loads(a.definitions.read_text('utf-8'))
    corpus=json.loads(a.corpus.read_text('utf-8'))
    protected={str(f):sha(f) for f in REPORT.glob('human-review*.csv')}
    protected.update({str(f):sha(f) for f in (a.candidates,a.definitions,a.corpus)})
    with (REPORT/'human-review-ontology46.csv').open(encoding='utf-8-sig',newline='') as f:ratings={r['item']:r for r in csv.DictReader(f)}
    pairs=[];changed=[];start=time.perf_counter()
    for definition in definitions['units']:
        key=definition['id'];units=resolve(definition,corpus['units'])
        for i,old in enumerate(old_data['units'][key]['candidates'],1):
            item=f'O-{key}-{i:02d}';new=normalize(old,units);verify_references(new,units)
            different={field for field in CORE if old[field]!=new[field]}
            pair={'item':item,'before':old,'after':new,'changed_core_fields':sorted(different),
                'source_units':units,'source_definitions':definition['sources'],'new_human_decision':None}
            pairs.append(pair)
            if different:changed.append(pair)
    elapsed=time.perf_counter()-start
    expected={item for item,r in ratings.items() if r['decision']!='正确'}
    assert len(pairs)==46 and {pair['item'] for pair in changed}==expected
    assert len(changed)==14 and len({p['after']['candidate_id'] for p in pairs})==46
    a.output.mkdir(parents=True)
    dump(a.output/'candidates-normalized.json',{'format':VERSION,'status':'machine_candidates_unreviewed','candidates':[p['after'] for p in pairs]})
    dump(a.output/'paired-comparison-46.json',pairs)
    shutil.copy2(a.candidates,a.output/'candidates-original.json')
    shutil.copytree(PRIVATE/'source-supplement',a.output/'source-supplement')
    (a.output/'batches').mkdir()
    header=('# 本体候选规范化：完整开发复核材料\n\n'
        '只读历史候选的离线规则后处理，不是新模型或新资料实验。旧评分保持；新评分全部空白。'
        '判定选正确/部分正确/错误/无法判断，并附理由。源片段相同不保证语义正确，组合标签不冒充逐字原文。'
        '本候选新类型不直接兼容旧Candidate枚举，也未接入正式本体引擎；质量评分不授权正式更新。\n\n')
    blocks=[];review_rows=[]
    for i,pair in enumerate(changed):
        batch=f'N{i//7+1:02d}';item=pair['item'];new=pair['after']
        block=f"## {item}\n\n原本人缺陷理由：{ratings[item]['reason']}\n\n"
        block+='### 原候选\n\n```json\n'+json.dumps(pair['before'],ensure_ascii=False,indent=2)+'\n```\n\n'
        block+='### 新候选（全部字段与精确来源引用）\n\n```json\n'+json.dumps(new,ensure_ascii=False,indent=2)+'\n```\n\n'
        block+='### 该单元全部完整来源（含引用之外的上下文）\n\n'
        for u in pair['source_units']:
            block+=f"{u['source_id']} · {u['locator']} · {u.get('evidence_kind')}\n\n```text\n{u['text']}\n```\n\n"
        block+='来源身份与版本：\n\n```json\n'+json.dumps(pair['source_definitions'],ensure_ascii=False,indent=2)+'\n```\n\n'
        block+='原件、表头与元数据：[补件](../source-supplement/README.md)。\n\n本人判定：____；理由：____\n\n'
        blocks.append(block);review_rows.append({'item':item,'batch':batch,'candidate_id':new['candidate_id'],
            'review_kind':'开发者人工审核','reviewer':'','decision':'','reason':'','recorded_at':''})
    for i in range(0,len(blocks),7):
        (a.output/'batches'/f'N{i//7+1:02d}.md').write_text(header+''.join(blocks[i:i+7]),'utf-8')
    (a.output/'全部14项完整材料.md').write_text(header+''.join(blocks).replace('../source-supplement/','source-supplement/'),'utf-8')
    with (a.output/'统一审核表.csv').open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(review_rows[0]));w.writeheader();w.writerows(review_rows)
    for path,h in protected.items():assert sha(Path(path))==h,path
    result={'label':'offline_development_comparison','method':'source_bound_rule_postprocessing',
        'corpus_units':len(corpus['units']),'before_candidates':46,'after_candidates':46,'changed_core_candidates':14,'changed_items':[p['item'] for p in changed],
        'unchanged_core_candidates':32,'field_reference_errors':0,'new_human_scores':0,'formal_integration':False,
        'extraction_seconds':round(elapsed,6),'before_same_condition_seconds':None,'new_api_calls':0,'model_tokens':0,
        'model_cost_usd':0,'production_changes':0,'source_candidates_sha256':sha(a.candidates),
        'definitions_sha256':sha(a.definitions),'corpus_sha256':sha(a.corpus),
        'normalizer_sha256':sha(ROOT/'scripts/normalize_ontology_candidates.py'),
        'new_candidates_sha256':sha(a.output/'candidates-normalized.json'),'review_material_sha256':sha(a.output/'全部14项完整材料.md'),
        'protected_files_unchanged':len(protected),'review_batches':{'N01':7,'N02':7},
        'note':'14个开发缺陷的定向后处理，32原核心记录逐字不变；不说明新版正确率、泛化、模型抽取质量或人工效率。'}
    dump(a.output/'receipt.json',result)
    with ZipFile(zpath,'x',ZIP_DEFLATED) as z:
        for f in sorted(a.output.rglob('*')):
            if f.is_file():z.write(f,str(Path(a.output.name)/f.relative_to(a.output)))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

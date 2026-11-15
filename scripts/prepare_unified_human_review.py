"""Bundle existing evidence and blank review rows; no model, approval or deployment."""
import argparse,csv,json,re,shutil,subprocess
from pathlib import Path
from hashlib import sha256
from zipfile import ZipFile,ZIP_DEFLATED
from generate_unit_candidates import resolve,check

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'docs/competition/dual-track-improvement-202611'
PRIVATE=ROOT/'.runtime/dual-track-improvement-202611'


def digest(p):return sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n','utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidates',type=Path,required=True);p.add_argument('--corpus',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    zip_path=a.output.with_suffix('.zip')
    if a.output.exists() or zip_path.exists():raise ValueError('EXISTING_REVIEW_PRESERVED')
    stored=json.loads((ROOT/'evidence/lowresource-20261105/method-b-candidates-receipt.json').read_text('utf-8'))
    assert digest(a.candidates)==stored['private_file_sha256']
    definitions=ROOT/'evaluation/lowresource-20261030/units.json'
    archived_definition=subprocess.run(['git','show','73ed5fc:evaluation/lowresource-20261030/units.json'],cwd=ROOT,capture_output=True,check=True).stdout
    assert sha256(archived_definition).hexdigest()==stored['units_definition_sha256']
    # The later top-level release note changed; every actual unit is identical.
    assert json.loads(archived_definition)['units']==json.loads(definitions.read_text('utf-8'))['units']
    protected={str(f):digest(f) for f in REPORT.glob('human-review*.csv')}
    corpus=json.loads(a.corpus.read_text('utf-8'));plan=json.loads(definitions.read_text('utf-8'))
    data=json.loads(a.candidates.read_text('utf-8'))
    a.output.mkdir(parents=True);(a.output/'batches').mkdir();(a.output/'evidence').mkdir()
    shutil.copytree(PRIVATE/'source-supplement',a.output/'source-supplement')
    shutil.copy2(a.candidates,a.output/'evidence/ontology-candidates-original.json')
    (a.output/'evidence/ontology-definition-original.json').write_bytes(archived_definition)
    dump(a.output/'evidence/ontology-unit-definitions.json',{'schema_version':plan['schema_version'],'units':plan['units']})
    rows=[];chunks=[];batch_counts={};checks={}
    def add(item,batch,kind,material):
        rows.append({'item':item,'batch':batch,'review_kind':'开发者人工审核','task_kind':kind,
            'reviewer':'','decision':'','reason':'','required_facts_total':'','correct_facts_covered':'',
            'claims_total':'','claims_supported':'','false_stop':'','review_minutes':'','material':material})
    for definition in plan['units']:
        key=definition['id'];texts=resolve(definition,corpus['units']);candidates=data['units'][key]['candidates']
        assert len(candidates)==stored['units'][key]['candidates'] and len(candidates)<=10
        checked=check(candidates,texts);checks[key]={'candidates':len(candidates),'normalized_quote_matches':sum(c['quote_in_source'] for c in checked)}
        content=f"# 本体 {key}：{len(candidates)}项\n\n目标：{definition['target']}\n\n"
        content+='这是历史模型候选的开发者人工质量审核，不是原交叉分配实验，不计纯人工对照或独立效率成绩。\n\n'
        content+=f"原定义开发接触标记：{definition.get('seen_in_development')}；{definition.get('development_note','本轮不据历史标记推定当前独立性。')}\n\n"
        content+='## 全部完整来源及定位\n\n'
        for u in texts:
            content+=f"### {u['source_id']} · {u['locator']}\n\n种类：{u.get('evidence_kind')}；文本SHA：{u['text_sha256']}\n\n```text\n{u['text']}\n```\n\n"
        content+='源定义（含URL、原件散列、版本与定位）：\n\n```json\n'+json.dumps(definition['sources'],ensure_ascii=False,indent=2)+'\n```\n\n'
        if key=='U6':content+='扫描AI转录不等于原件；请对照[第5页](../source-supplement/scan-5.png)、[第6页](../source-supplement/scan-6.png)和[PDF原件](../source-supplement/HN-MEASURES-2021-PDF.pdf)。\n\n'
        for i,c in enumerate(candidates,1):
            item=f'O-{key}-{i:02d}'
            content+=f"## {item}\n\n```json\n"+json.dumps(c,ensure_ascii=False,indent=2)+'\n```\n\n'
            content+='本次机械核验（忽略空白）引文匹配：'+str(checked[i-1]['quote_in_source'])+'；不是人工正确结论。\n\n判定：____；理由或修正：____\n\n'
            add(item,'O-'+key,'本体候选质量',f'batches/O-{key}.md')
        name=f'batches/O-{key}.md';(a.output/name).write_text(content,'utf-8');chunks.append((name,content));batch_counts['O-'+key]=len(candidates)
    for old in sorted((PRIVATE/'crossdoc-24').glob('batch-C*.md')):
        text=old.read_text('utf-8');batch=old.stem.removeprefix('batch-');name='batches/'+batch+'.md'
        # Existing source-supplement location remains valid from batches/.
        text+='\n\n原件、Excel列头/坐标及网页元数据：[完整补件](../source-supplement/README.md)。\n'
        (a.output/name).write_text(text,'utf-8');chunks.append((name,text));batch_counts[batch]=9
        for q in re.findall(r'^## (X\d+)\s*$',text,re.M):
            for method in 'ABC':add(q+'-'+method,batch,'历史跨文档输出',name)
    text=(PRIVATE/'v3-human-review/V3-02.md').read_text('utf-8')
    block=text.split('## 14. ',1)[1].split('\n## 15.',1)[0]
    name='batches/F01.md';content='# F01：过滤合理性\n\n## 14. '+block
    (a.output/name).write_text(content,'utf-8');chunks.append((name,content));add('F-14','F01','过滤合理性',name);batch_counts['F01']=1
    skill=PRIVATE/'offline-finish-v3-r2/residence-skill'
    name='batches/S01.md';content='# S01：Skill结构方案质量\n\n只判断方案是否准确、范围及引用是否清楚；本表判断不生成结构批准文件，不授权运行、发布或写入正式状态。真实复用效果尚未发生。\n\n'
    for locator in ('第一百四十五条','第一百五十条'):
        u=next(u for u in corpus['units'] if u['source_id']=='HN-NORM-PUBLISHER' and u['locator']==locator)
        content+=f"## 完整原文：{locator}\n\n```text\n{u['text']}\n```\n\n"
    for file in ('plan.json','migration-readiness.json'):
        content+=f'## {file}\n\n```json\n'+(skill/file).read_text('utf-8')+'\n```\n\n'
    content+='## 完整Skill\n\n```markdown\n'+(skill/'skills/govkb-residence-endorsement-archive/SKILL.md').read_text('utf-8')+'\n```\n\n'
    content+='判定：____；理由：____\n'
    (a.output/name).write_text(content,'utf-8');chunks.append((name,content));add('S-01','S01','Skill结构质量（非业务批准）',name);batch_counts['S01']=1
    claims=[('D-01','编排与MCP/Skill集成','实现逐条修复反馈、来源版本隔离及五工具归档摘录方案；离线检查通过。增强后的原生运行及真实迁移尚未验收。'),
    ('D-02','资产业务价值','保留历史30题负结果和24题三路输出；已完成事实卡开发者审核及已知缺陷后续复核。不将卡片正确率当作问答正确率，不宣称新方案独立优于RAG。'),
    ('D-03','低资源本体效率','事实卡与模型本体候选分开；原63卡片及后续修复有本人记录，46条历史模型候选仍待本次审核。未形成新方法独立语义或人工耗时优势。'),
    ('D-04','体验与Skill沉淀','已准备可复用模板和居住证签注归档摘录结构。4份真实OCR本人审核为2正确、2部分正确。未宣称纯配置迁移、真实复用效率或多模态最终问答提升。')]
    name='batches/D01.md';content='# D01：四维度材料表述审核\n\n只核以下草稿是否被存证支持、是否夸大；不审核赛事实时规则，不发布初赛材料。\n\n'
    for item,title,claim in claims:
        content+=f'## {item} · {title}\n\n拟用表述：{claim}\n\n判定：____；理由：____\n\n';add(item,'D01','比赛材料表述准确性',name)
    content+='## 核对证据\n\n见[证据目录](../evidence/)，包括所有版本统计、离线运行回执、已归档官方摘录。原规则摘录与历史结果不是本轮重新确认官网。\n'
    (a.output/name).write_text(content,'utf-8');chunks.append((name,content));batch_counts['D01']=4
    for file in ['human-review-summary.json','human-review-v3-summary.json','human-review-v4-summary.json',
                 'human-review-v4_1-summary.json','human-review-v4_2-summary.json','ocr-human-review-summary.json',
                 'offline-finish-v3-r2.json','fact-cards-v4_2-offline.json','baseline.md','evolution-skill-results.md',
                 'crossdoc-results.md','score-evidence.md']:
        target=file+'.txt' if file.endswith('.md') else file
        shutil.copy2(REPORT/file,a.output/'evidence'/target)
    shutil.copy2(ROOT/'docs/赛题3资料/官方赛题摘录.md',a.output/'evidence/官方赛题归档摘录.txt')
    for f in (ROOT/'.runtime').glob('dual-track-*.xml'):shutil.copy2(f,a.output/'evidence'/f.name)
    for f in (ROOT/'.runtime').glob('fact-cards-v4*-tests.xml'):shutil.copy2(f,a.output/'evidence'/f.name)
    # Preserve full corpus text for checking reference facts absent from a model's citations.
    shutil.copy2(a.corpus,a.output/'evidence/source-corpus.json')
    assert len(rows)==124 and all(n<=10 for n in batch_counts.values())
    with (a.output/'统一审核表.csv').open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    guide=('# 统一人工审核包\n\n124个空白质量审核任务：46条模型本体候选、72份历史跨文档输出、1项过滤、1项Skill结构、4项比赛表述。'
        '共17批，各批最多10项。全部可一次回交，不需要逐批向助手索取。原151次已完成判断不重复要求。\n\n'
        '打开[全部材料](全部审核材料.md)或下列分批文件，填写[统一审核表](统一审核表.csv)。'
        '只填本人真实判断：正确/部分正确/错误/无法判断；非正确写理由。reviewer可留空，回交后记录本人身份。'
        '跨文档逐事实覆盖/引文支持/误停止字段有把握再填；未填保持未知，不反推。review_minutes只填实际计时，不用录入时间冒充耗时。\n\n'
        '这次统一审核不是独立或双人盲评。46条候选开放后同一审核人再做同资料纯人工对照会有暴露偏差，不能声称独立效率收益。'
        '旧方法A/B分配保留历史属性，用户本人统一审核采用本轮开发口径。质量判定不等于业务批准，不会据此生成正式审批或部署。\n\n')
    for name,_ in chunks:guide+=f'- [{name}]({name})\n'
    guide+='\n## 七类任务对应状态\n\n'
    guide+='1. 历史本体46条：已恢复并按存证核散列，本包O-U1至O-U6完整发审。\n'
    guide+='2. 独立留出：题目与参考未建立，没有输出可验收；未伪造独立性，本包不能替代。\n'
    guide+='3. 多模态最终问答/Excel/HTML：本包历史跨文档可核既有融合输出；新的同条件识别后问答对照、模糊失败实验尚未执行。既有4份OCR已审，不重复。\n'
    guide+='4. 新方法本体与修正耗时：O组可核历史语义，不能替代新资料方法对照；未真实计时则留空。\n'
    guide+='5. 更新审批/切换/回滚：没有当前具备digest、预期head和完整影响的真实候选可批；不在本包索取空泛批准，当前状态不变。\n'
    guide+='6. Skill复用：S01只审完整原文与结构方案。真实执行、配置计时和原生部署未发生；不从正确判断推定授权。\n'
    guide+='7. 四维度材料：D01审可核对的四条保守草稿；正式Word/包最终审核仍待效果证据补齐。\n\n'
    guide+='缺失的真实调用、外发、生产更新和发布仍需具体范围及预算单独批准，本次未执行。\n'
    (a.output/'开始这里.md').write_text(guide,'utf-8')
    (a.output/'全部审核材料.md').write_text(guide+'\n\n'+'\n\n'.join(content.replace('../source-supplement/','source-supplement/').replace('../evidence/','evidence/') for _,content in chunks),'utf-8')
    for path,h in protected.items():assert digest(Path(path))==h,path
    files={str(f.relative_to(a.output)):digest(f) for f in sorted(a.output.rglob('*')) if f.is_file()}
    manifest={'label':'developer_unified_review','items':124,'completed':0,'batches':batch_counts,
        'candidate_original_sha256':digest(a.candidates),'units_definition_current_sha256':digest(definitions),
        'units_definition_original_sha256':sha256(archived_definition).hexdigest(),'unit_definitions_unchanged':True,
        'source_corpus_sha256':digest(a.corpus),'quote_checks':checks,'files_sha256':files,
        'old_ledgers_unchanged':len(protected),'new_api_calls':0,'production_changes':0,'approvals_generated':0}
    dump(a.output/'manifest.json',manifest)
    with ZipFile(zip_path,'x',ZIP_DEFLATED) as z:
        for f in sorted(a.output.rglob('*')):
            if f.is_file():z.write(f,str(Path(a.output.name)/f.relative_to(a.output)))
    print(json.dumps({'items':124,'batches':batch_counts,'quote_checks':checks,'zip_bytes':zip_path.stat().st_size,
        'zip_sha256':digest(zip_path),'new_api_calls':0},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

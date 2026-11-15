"""Export the finished offline implementation and its real, bounded evidence.

No API calls, upload grants, human decisions, live service startup, production
writes or automatic promotions. Existing samples/reviews/results are immutable.
"""
import argparse
from collections import Counter
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import extract_fact_cards as v1
import extract_fact_cards_v2 as v2
import extract_fact_cards_v3 as v3
import workflow_template as template
from gov_mcp.archive_skill_preview import ArchiveSkillPreview, FORMAT, code_fingerprints, digest
from gov_mcp.multimodal_evidence import upload_plan, plan_fingerprint


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--run-name',default='offline-finish-v3',choices=('offline-finish-v3','offline-finish-v3-r2'))
    args=parser.parse_args()
    report=ROOT/'docs/competition/dual-track-improvement-202611'
    private=ROOT/'.runtime/dual-track-improvement-202611'
    out=private/args.run_name
    receipt_path=report/(args.run_name+'.json')
    if out.exists() or receipt_path.exists():raise ValueError('EXISTING_DELIVERY_PRESERVED')
    protected=[private/'cards.jsonl',private/'sample.json',report/'human-review.csv',report/'human-review-summary.json',
               report/'review-manifest.json',report/'fact-cards-v2-offline.json']
    preserved={str(p.relative_to(ROOT)):file_sha(p) for p in protected}
    corpus=json.loads(args.corpus.read_text('utf-8'));units=corpus['units']
    baseline_summary=json.loads((ROOT/'evidence/fact-cards/summary-20261115-1.json').read_text('utf-8'))
    assert v1.frozen_qa.sha(json.dumps(units,ensure_ascii=False,sort_keys=True))==baseline_summary['corpus_unit_sha256']
    assert file_sha(ROOT/'scripts/extract_fact_cards.py')==baseline_summary['script_sha256']
    before=time.perf_counter();cards=v3.extract(units,corpus['manifest']['sources']);elapsed=time.perf_counter()-before
    assert not v1.verify_cards(cards,units) and not v3.verify_field_references(cards,units)
    assert len(cards)==len({c['semantic_id'] for c in cards})
    assert all(c['review_state']=='machine_candidate_unreviewed' and c['formal_approval'] is False for c in cards)
    sample=json.loads((private/'sample.json').read_text('utf-8'))
    by_id={c['card_id']:c for c in cards}
    matched=[]
    for number,old in enumerate(sample,1):
        after=by_id.get(old['card_id'])
        if after is None:
            alternatives=[c for c in cards if (c['source_id'],c['locator'])==(old['source_id'],old['locator'])
                          and ((old['type']=='table_cell' and c['type']=='table_cell' and c['value']['cell']==old['value']['cell'])
                               or (old['type']=='duration' and c['type']=='count' and c['span'][0]==old['span'][0] and c['value']['unit']=='周岁'))]
            assert len(alternatives)<=1
            after=alternatives[0] if alternatives else None
        matched.append({'number':number,'before':old,'after':after,'new_human_decision':None})
    assert [x['number'] for x in matched if x['after'] is None]==[14]
    # Previously correct scalar/coordinate values must not be silently changed.
    import csv
    with (report/'human-review.csv').open(encoding='utf-8-sig',newline='') as stream:ratings=list(csv.DictReader(stream))
    checked_correct=0
    for pair,rating in zip(matched,ratings):
        if rating['decision']=='正确':
            assert pair['after'] is not None and pair['before']['value']==pair['after']['value']
            checked_correct+=1
    assert checked_correct==15
    out.mkdir()
    (out/'cards-v3.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cards),'utf-8')
    dump(out/'sample-comparison-63.json',matched)
    reading=['# V3候选全63项：原抽取与改后对照\n\n本页供开发者人工审核；新版尚无评分，旧评分不迁移。'
             '每个新字段均有原文字符引用；字段非空不等于语义正确。第14项为过滤项。'
             '按本人要求一次给全，仍以每9项分组。[原件及Excel结构](../source-supplement/README.md)。\n\n']
    by_unit={(u['source_id'],u['locator']):u for u in units}
    for pair in matched:
        n=pair['number'];old=pair['before']
        if (n-1)%9==0:reading.append(f'# 分组{(n-1)//9+1}\n\n')
        reading.append(f"## {n}. {old['source_id']} · {old['locator']}\n\n原值：`{json.dumps(old['value'],ensure_ascii=False)}`\n\n"
                       f"原对象：{'、'.join(old['subjects']) or '未识别'}\n\nV3候选（未人工核验）：\n\n```json\n"
                       +json.dumps(pair['after'],ensure_ascii=False,indent=2)+'\n```\n\n完整冻结单元：\n\n'
                       +by_unit[old['source_id'],old['locator']]['text']+'\n\n新版判定：____；理由：____\n\n')
    (out/'human-review-v3-all-63.md').write_text(''.join(reading),'utf-8')
    # A genuine new matter uses the same five methods, with a new bound source plan.
    skillroot=out/'residence-skill';skillroot.mkdir()
    (skillroot/'archive-skill-workspace.json').write_text(json.dumps({'format':FORMAT}),'utf-8')
    nodes=[]
    for node_id,locator in [('registration_materials','第一百四十五条'),('endorsement_rule','第一百五十条')]:
        found=[u for u in units if u['source_id']=='HN-NORM-PUBLISHER' and u['locator']==locator]
        assert len(found)==1
        nodes.append({'id':node_id,'source_id':found[0]['source_id'],'locator':locator,
                      'text_sha256':found[0]['text_sha256'],'operator':'exact_excerpt'})
    plan={'format':FORMAT,'scenario_id':'henan_residence_endorsement_archive','implementation':code_fingerprints(),
          'corpus_path':str(args.corpus.resolve()),'corpus_file_sha256':file_sha(args.corpus),
          'original_paths':{'HN-NORM-PUBLISHER':str((args.source_dir/'HN-NORM-PUBLISHER.html').resolve())},'nodes':nodes}
    dump(skillroot/'plan.json',plan)
    dump(skillroot/'ontology-binding.json',{'profile':FORMAT,'plan_path':str((skillroot/'plan.json').resolve())})
    profile=template.load_profile(ROOT/'config/skill-templates/profiles/henan-residence-endorsement.json')
    started=time.perf_counter()
    template.write_skill(profile,skillroot/'skills')
    (skillroot/'PROTOCOL.txt').write_text(template.render_protocol(profile),'utf-8')
    provisioner=template.profile_provisioner(profile,skillroot,skillroot/'future-receipts')
    first=provisioner.first_action()
    assert plan['scenario_id'] in first and profile['skill_name'] in first
    preview=ArchiveSkillPreview(skillroot,skillroot/'plan.json')
    preflight=preview.preflight()
    signatures={method:list(inspect.signature(getattr(preview,method)).parameters) for method in ('open_query','retrieve','verify','state','finalize')}
    assert signatures=={'open_query':['scenario_id'],'retrieve':['task_id'],'verify':['task_id'],'state':['task_id'],'finalize':['task_id']}
    try:preview.open_query(plan['scenario_id'])
    except ValueError as error:assert str(error)=='EXPLICIT_HUMAN_STRUCTURE_REVIEW_REQUIRED'
    else:raise AssertionError('Unapproved real-source plan must never execute')
    migration={'preflight':preflight,'shared_method_signatures':signatures,'tool_names':sorted(provisioner.TOOLS),
               'automated_prepare_seconds':time.perf_counter()-started,'human_config_minutes':None,
               'human_operation_steps':['核对原件和两条完整条款','本人确认结构计划及其散列',
                                        '另行批准后登记原生草稿并导入Skill','在界面执行并回交截图和计时'],
               'agents_created':0,'live_deployment':False,'real_human_approval':False,
               'execution_operator':'exact_excerpt_not_general_ontology_inference'}
    dump(skillroot/'migration-readiness.json',migration)
    # First authorized experiment can use two already-rendered public page images.
    scan=next(s for s in corpus['manifest']['sources'] if s['source_id']=='HN-MEASURES-2021-PDF')
    imageplans=[]
    for page in (5,6):
        image=args.source_dir/f'scan-{page}.png'
        for purpose in ('text_recognition','rich_layout_recognition'):
            item=upload_plan(image,scan['source_id'],scan['url'],scan['original_sha256'],page,purpose)
            item['image_path']=str(image.resolve())
            item['plan_sha256_for_approval']=plan_fingerprint({k:v for k,v in item.items() if k!='image_path'})
            imageplans.append(item)
    ocr={'status':'AWAITING_EXPLICIT_USER_UPLOAD_AND_COST_APPROVAL','max_ocr_calls':4,'max_uploaded_images':2,
         'max_recognition_pages':4,'max_qa_calls':0,'automatic_retries':0,'proposed_max_usd':1,
         'conservative_reserved_usd_per_page':.01,'plans':imageplans,
         'risk':'图片外发至DeepSeek官方；超时也可能识别并计费；目前未构造任何上传批准Grant'}
    dump(out/'ocr-approval-plan.json',ocr)
    dump(out/'ocr-approval-template.json',{
        'confirmed_by_user':False,'public_non_sensitive_confirmed':False,'approval_reference':'',
        'run_id':'ocr-four-pages-20261115','plan_file_sha256':file_sha(out/'ocr-approval-plan.json'),
        'max_calls':4,'max_pages':4,'max_usd':1,
        'notice':'空白批准模板，不是用户批准；只有用户明确批准后才可填写或运行。'})
    # Summarize unique test cases using the latest available result for each ID.
    xmls=['dual-track-finish-tests.xml','dual-track-finish-recheck.xml','dual-track-final-integration.xml',
          'dual-track-skill-final.xml','dual-track-semantic-delivery.xml','dual-track-rejection-cli-final.xml',
          'dual-track-final-hardening.xml','dual-track-bounded-ocr-final.xml','dual-track-version-final.xml']
    outcomes={}
    for name in xmls:
        tree=ET.parse(ROOT/'.runtime'/name)
        for test in tree.getroot().iter('testcase'):
            outcomes[test.get('classname'),test.get('name')]='failed' if test.find('failure') is not None or test.find('error') is not None else 'skipped' if test.find('skipped') is not None else 'passed'
    assert set(outcomes.values())=={'passed'}
    files=['scripts/extract_fact_cards_v3.py','scripts/finish_dual_track_offline.py','scripts/run_ontology_evolution.py','scripts/run_bounded_ocr.py',
           'src/gov_mcp/frozen_qa_flow_v3.py','src/gov_mcp/frozen_qa_flow_server.py','src/gov_mcp/ontology_evolution.py',
           'src/gov_mcp/ontology_crossdoc_preview_server.py','src/gov_mcp/archive_skill_preview.py','src/gov_mcp/multimodal_evidence.py',
           'config/skill-templates/profiles/henan-residence-endorsement.json']
    summary={'scope':'offline_implementation_complete_real_acceptance_pending','corpus_units':len(units),
             'candidate_cards':len(cards),'candidate_cards_file_sha256':file_sha(out/'cards-v3.jsonl'),
             'field_reference_errors':len(v3.verify_field_references(cards,units)),
             'exact_span_errors':len(v1.verify_cards(cards,units)),
             'semantic_gaps':dict(Counter(g for c in cards for g in c['semantic_gaps'])),
             'review_sample_retained':62,'review_sample_filtered':[14],
             'sample_candidates_without_object':sum(not p['after']['subjects'] for p in matched if p['after']),
             'previously_correct_values_preserved':checked_correct,'new_human_scores':0,
             'new_version_accuracy':None,'model_calls':0,'upload_calls':0,'formal_state_changes':0,
             'candidate_extraction_seconds':elapsed,'tests_unique_latest':dict(Counter(outcomes.values())),
             'test_receipts_sha256':{name:file_sha(ROOT/'.runtime'/name) for name in xmls},
             'source_sha256':{name:file_sha(ROOT/name) for name in files},
             'protected_evidence_sha256':preserved,'skill_migration':migration,
             'ocr_plan_status':ocr['status'],'independent_holdout_status':'NOT_ESTABLISHED',
             'merge_recommendation':'WAIT_FOR_HUMAN_AND_REAL_HOLDOUT_ACCEPTANCE'}
    assert preserved=={str(p.relative_to(ROOT)):file_sha(p) for p in protected}
    summary['protected_evidence_unchanged']=True
    dump(receipt_path,summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

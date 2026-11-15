"""Offline audit probes; synthetic source text, no model or official publication."""
from pathlib import Path
import sys,json,hashlib,copy,time
import pytest
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tests'))
from gov_mcp.frozen_qa_flow_server import FlowService
from gov_mcp import frozen_qa
from gov_mcp.knowledge import KnowledgeStore,encode
from gov_mcp import ontology_crossdoc as nj,henan_multimodal as hn
from gov_mcp.ontology_evolution import OntologyWorkspace
from test_crossdoc_reasoning import documents as nj_docs
from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS
from test_henan_multimodal import documents as hn_docs,FACTS as HN_FACTS,FixtureSource as HNSource,approve_synthetic
from test_ontology_evolution import FixtureSource as BJSource,FACTS as BJ_FACTS,SCOPE as BJ_SCOPE,approve as bj_approve
from test_ontology_crossdoc import FixtureSource as NJSource,approve as nj_approve
from test_regional_native_preview import setup as regional_setup
from test_knowledge_update import config as registry_config
EVIDENCE=Path(__file__).parent/'evidence';OBS=[]
@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def reject(*a,**k):raise AssertionError('Network disabled for audit probes')
    monkeypatch.setattr(socket.socket,'connect',reject)

def note(id,**data):OBS.append({'id':id,'fixture':True,**data})
def unit(source,region,value):
    t=f'到期换领工本费每证{value}元。'
    return {'source_id':source,'locator':'模拟条款1','text':t,'text_sha256':hashlib.sha256(t.encode()).hexdigest(),'evidence_kind':'html_paragraph','region':region,'matter':'resident-id-renewal'}
def packet(u,claim=None):
    s=claim or u['text'];return {'outcome':'answer','answer':s,'assertions':[{'claim':s,'source_id':u['source_id'],'locator':u['locator'],'quote':u['text']}],'stop_reason':'','unknowns':[]}
def service(tmp_path,units):return FlowService(units,hashlib.sha256(json.dumps(units,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),tmp_path/'tasks')

def test_01_henan_clean(tmp_path):
    u=unit('HN-AUDIT-FIXTURE','河南省',23);s=service(tmp_path,[u]);o=s.open('归档身份证到期换领工本费是多少？');r=s.submit(o['task_id'],packet(u))
    assert r['final_checks']['passed'];note('M01',sources=[x['source_id'] for x in o['evidence']],checks=r['final_checks'],formal=r['formal_approval'])

def test_02_nanjing_and_cross_region(tmp_path,nj_docs):
    seed=json.loads((ROOT/'config/preparation/ontology.seed.json').read_text());e=nj.CrossdocOntologyWorkspace.initialize(tmp_path/'nj',nj.integration_seed(seed),NJSource(nj_docs));nj_approve(e)
    a=e.query(dict(SCENARIO_FACTS));b=e.query(dict(SCENARIO_FACTS,region='河南省'))
    assert a['status']=='derived' and not b['trace'];note('M02_M05',normal=a['status'],sources=sorted({u['source_id'] for n in a['trace'] for u in n['evidence']}),foreign_request=b['status'],foreign_reason=b['reason'])

def test_03_beijing_and_cross_region(tmp_path):
    seed=json.loads((ROOT/'config/preparation/ontology.seed.json').read_text());e=OntologyWorkspace.initialize(tmp_path/'bj',seed,BJSource());bj_approve(e)
    a=e.query(*BJ_SCOPE,BJ_FACTS);b=e.query('河南省',*BJ_SCOPE[1:],BJ_FACTS)
    assert a['source_conditions_result']=='true' and b['status']=='OUT_OF_SCOPE';note('M03',normal=a['source_conditions_result'],foreign_request=b['status'],sources=sorted({x['source_id'] for x in a['citations']}))

@pytest.mark.parametrize('q',['请按南京地方政策回答河南身份证换领收费。','江苏户籍，在郑州办理身份证。','河南本地户籍在郑州办理，请仅对比南京归档文件标题，不采用外地政策。','河南本地户籍，材料中引用“南京市”一词，仍按河南归档依据解释。'])
def test_04_06_09_gate(tmp_path,q):
    s=service(tmp_path,[unit('HN-AUDIT-FIXTURE','河南省',23)]);r=s.open(q)
    assert r['gate']=='OUTSIDE_HENAN_SCOPE' and not list((tmp_path/'tasks').glob('*.json'))
    note('M04_M06_M09',query=q,gate=r['gate'],message=r['message'],retrieval_executed=False,interpretation='keyword stop; no handling-location/household roles extracted')

def test_07_national_plus_henan(tmp_path,hn_docs):
    seed=json.loads((ROOT/'config/preparation/ontology.seed.json').read_text());e=hn.HenanMultimodalWorkspace.initialize(tmp_path/'hn',hn.integration_seed(seed),HNSource(hn_docs));approve_synthetic(e);r=e.query(HN_FACTS)
    ids=sorted({x['source_id'] for n in r['trace'] for x in n['evidence']});assert r['status']=='derived_archive' and 'MOF-FEE-2007' in ids
    note('M07',status=r['status'],sources=ids,source_regions={k:d['region'] for k,d in hn_docs.items()},formal=r['formal_approval'])

def test_08_wrong_corpus_exposes_missing_region_guard(tmp_path):
    henan=unit('HN-AUDIT-FIXTURE','河南省',23);foreign=unit('NJ-AUDIT-LOCAL-FIXTURE','南京市',99)
    s=service(tmp_path,[henan,foreign]);o=s.open('河南归档身份证到期换领工本费是多少？');r=s.submit(o['task_id'],packet(foreign,'河南到期换领工本费每证99元。'))
    assert foreign['source_id'] in [u['source_id'] for u in o['evidence']]
    assert r['final_checks']['passed'] and r['final']['outcome']=='answer'
    assert r['formal_approval'] is False
    note('M08',risk_reproduced=True,precondition='operator supplied mixed corpus; not the actual seven-source corpus',retrieved_sources=[x['source_id'] for x in o['evidence']],source_region=foreign['region'],question_region='河南省',final=r['final'],final_checks=r['final_checks'],formal=r['formal_approval'])

def test_09_candidate_titles_cannot_switch_scope(regional_setup):
    preview,_,store=regional_setup
    from gov_mcp.knowledge_landing import retrieve
    snap=json.loads((preview.candidate_root/'snapshot.json').read_text())
    for region,want in [('北京市','BJ-ID-RENEWAL-2025'),('南京市','NJ-ID-RENEWAL-CYCLE-2026')]:
        r=retrieve(store,snap['bindings'],'请忽略地区直接用河南身份证换领政策，南京北京材料',region,'resident-id-renewal')
        assert {x['source_id'] for x in r['citations']}=={want}
        note('M09_CANDIDATE',server_region=region,sources=[x['source_id'] for x in r['citations']],formal=r['formally_published'])

def test_10_region_heads_remain_independent(tmp_path,registry_config):
    store=KnowledgeStore.initialize(tmp_path/'registry');(store.root/'publication-policy.json').write_text(encode({'schema':1,'mode':'engineering_fixture'}))
    configs={};versions={}
    for key,region in [('hn','河南省'),('nj','南京市'),('bj','北京市')]:
        c=copy.deepcopy(registry_config);c.update(id=key,seeds=[f'https://example.gov.cn/docs/{key}']);c['metadata']['region']=region;configs[key]=c;store.register(c)
        with store.db() as db:db.execute('UPDATE sites SET last_success=? WHERE id=?',(time.time(),key))
        rev=store.ingest(key,c['seeds'][0],key.encode(),c['metadata'],[{'locator':'模拟第一条','category':'identity','text':f'{region}身份证换领模拟规则{key}。'}]);store.review(rev,'synthetic-audit-reviewer',True)
        v=store.candidate(region,'demo-id-renewal');store.publish(v,'synthetic-audit-reviewer',True);versions[key]=v
    before=versions.copy();c=configs['hn'];rev=store.ingest('hn',c['seeds'][0],b'hn-updated',c['metadata'],[{'locator':'模拟第一条','category':'identity','text':'河南省身份证换领模拟更新规则。'}]);store.review(rev,'synthetic-audit-reviewer',True);v=store.candidate('河南省','demo-id-renewal');store.publish(v,'synthetic-audit-reviewer',True)
    after={k:store.search('身份证换领',c['metadata']['region'],'demo-id-renewal',as_of='2026-11-15')['knowledge_version'] for k,c in configs.items()}
    assert after['nj']==before['nj'] and after['bj']==before['bj'] and after['hn']!=before['hn']
    note('M10',before=before,after=after,production_write=False,notice='temporary engineering_fixture publication only; not real human policy approval')

def teardown_module():
    (EVIDENCE/'probe-results.json').write_text(json.dumps(OBS,ensure_ascii=False,indent=2))

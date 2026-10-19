"""Exercise maintainer actions on synthetic data only, including a second real native update."""

from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
import time

import httpx

from nexent_api import ROOT, RUNTIME


def main():
    client=httpx.Client(base_url='http://127.0.0.1:8778',trust_env=False,timeout=30,headers={'Origin':'http://127.0.0.1:8778'})
    credentials=json.loads((RUNTIME/'accounts.json').read_text(encoding='utf-8'))['developer']
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'scope':'automated maintainer-operation acceptance on isolated synthetic data; not human policy approval',
        'real_jev_inference':False,'checks':{},'passed':False}
    checks=report['checks']
    def state():
        response=client.get('/api/state'); response.raise_for_status(); return response.json()
    def action(name,body):
        response=client.post('/api/actions/'+name,json=body); response.raise_for_status(); return response.json()
    def approve(s):
        candidates=[c for c in s['candidates'] if c['status']=='pending_review' and c.get('validation') and c['validation']['base_rules']==s['active_rules']]
        if not candidates: raise RuntimeError('Run verify_knowledge_native.py to prepare a current synthetic candidate')
        c=candidates[0]
        response=client.post('/api/actions/review',json={'candidate_id':c['id'],'expected_base':s['active_rules'],'accept':True,'acknowledge':False})
        checks['confirmation_required']=response.status_code==409
        return action('review',{'candidate_id':c['id'],'expected_base':s['active_rules'],'accept':True,'acknowledge':True})['version']
    try:
        client.post('/api/login',json=credentials).raise_for_status()
        before=state()
        assert before['synthetic_mode'] and all(s['data_class']=='synthetic' for s in before['configured_sources'])
        checks['initial_documents_exist']=bool(before['active_documents'])
        first_rules=approve(before)
        first_documents=before['active_documents']
        checks['confirmed_rules_queryable']=client.get('/api/search',params={'q':'申请表','kind':'rules'}).json()['status']=='OK'
        report['first_rules']=first_rules
        response=subprocess.run([sys.executable,str(ROOT/'scripts/verify_knowledge_native.py'),'--wait-seconds','600'],cwd=ROOT,timeout=650)
        if response.returncode: raise RuntimeError('Incremental native update failed')
        updated=state()
        checks['new_document_version']=updated['active_documents']!=first_documents
        checks['rules_not_auto_published']=updated['active_rules']==first_rules
        checks['outdated_rules_withheld']=client.get('/api/search',params={'q':'申请表','kind':'rules'}).json()['status']=='EMPTY'
        second_rules=approve(updated)
        current=state()
        report['second_rules']=second_rules
        report['document_versions']=[first_documents,current['active_documents']]
        checks['new_rules_queryable']=client.get('/api/search',params={'q':'申请表','kind':'rules'}).json()['status']=='OK'
        found=client.get('/api/search',params={'q':'申请表','kind':'documents'}).json()
        original=found['items'][0]
        original_path='/api/original/'+found['version']+'/'+original['raw_sha256']
        response=client.get(original_path)
        checks['original_hash_matches']=response.is_success and hashlib.sha256(response.content).hexdigest()==original['raw_sha256']
        checks['anonymous_original_denied']=httpx.get('http://127.0.0.1:8778'+original_path,trust_env=False).status_code==401
        action('rollback',{'kind':'documents','version':first_documents,'expected_base':current['active_documents']})
        checks['document_rollback']=state()['active_documents']==first_documents
        action('rollback',{'kind':'documents','version':current['active_documents'],'expected_base':first_documents})
        action('rollback',{'kind':'rules','version':first_rules,'expected_base':second_rules})
        checks['rules_rollback']=state()['active_rules']==first_rules
        action('rollback',{'kind':'rules','version':second_rules,'expected_base':first_rules})
        checks['restored_latest_rules_queryable']=client.get('/api/search',params={'q':'申请表','kind':'rules'}).json()['status']=='OK'
        # Paused manual checking runs once; unchanged source must not invoke a model.
        old_runs={r['id'] for r in state()['runs']}
        action('check',{})
        for _ in range(25):
            now=state()
            runs=[r for r in now['runs'] if r['id'] not in old_runs and r['status']!='running']
            if runs:
                checks['unchanged_skips_models']=runs[0]['status']=='no_model_work' and 'conversation_id' not in runs[0]
                break
            time.sleep(2)
        checks['unchanged_keeps_versions']=state()['active_documents']==current['active_documents'] and state()['active_rules']==second_rules
        service=json.loads((RUNTIME/'knowledge-demo/service-account.json').read_text(encoding='utf-8'))
        with httpx.Client(base_url='http://127.0.0.1:8778',trust_env=False,headers={'Origin':'http://127.0.0.1:8778'}) as other:
            checks['service_cannot_login_as_maintainer']=other.post('/api/login',json=service).status_code==401
        with httpx.Client(base_url='http://127.0.0.1:8775',trust_env=False,headers={'Origin':'http://127.0.0.1:8775'}) as production:
            production.post('/api/login',json=credentials).raise_for_status()
            clean=production.get('/api/state').json()
            checks['production_still_empty']=clean['configured_sources']==[] and clean['active_documents'] is None and clean['active_rules'] is None
        report['passed']=bool(checks) and all(checks.values()) and 'unchanged_skips_models' in checks
    except Exception as error: report['error_type']=type(error).__name__
    finally:
        try: client.post('/api/actions/pause',json={})
        except httpx.HTTPError: pass
        client.close()
        destination=ROOT/'evidence/knowledge-heartbeat/lifecycle.json'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

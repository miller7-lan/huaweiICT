"""Verify real native controller/children in an isolated synthetic/replayed-Jev runtime."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import time

import httpx

from nexent_api import ROOT, RUNTIME


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--wait-seconds",type=int,default=600)
    args=parser.parse_args()
    client=httpx.Client(base_url="http://127.0.0.1:8778",trust_env=False,timeout=20,headers={"Origin":"http://127.0.0.1:8778"})
    credentials=json.loads((RUNTIME/"accounts.json").read_text(encoding="utf-8"))["developer"]
    native=json.loads((RUNTIME/"knowledge-demo/native.json").read_text(encoding="utf-8"))
    report={"captured_at":datetime.now(timezone.utc).isoformat(),"scope":"real Nexent model orchestration; synthetic website and replayed Jev responses",
        "real_jev_inference":False,"official_website_verified":False,"agents":{r:native[r+"_id"] for r in native["versions"]},"versions":native["versions"],"passed":False}
    try:
        for _ in range(20):
            try:
                if client.get('/health').is_success: break
            except httpx.HTTPError: pass
            time.sleep(1)
        client.post('/api/login',json=credentials).raise_for_status()
        before=client.get('/api/state').json()
        existing={r["id"] for r in before["runs"]}
        fixture='<html><nav>模拟导航</nav><article id="policy"><p>模拟身份证事项需提交申请表。</p><p>工程检查批次：'+datetime.now(timezone.utc).isoformat()+'，不是业务规则。</p></article></html>'
        client.post('/api/actions/fixture',json={'html':fixture}).raise_for_status()
        client.post('/api/actions/resume',json={}).raise_for_status()
        client.post('/api/actions/check',json={}).raise_for_status()
        deadline=time.monotonic()+args.wait_seconds
        last=None
        while time.monotonic()<deadline:
            state=client.get('/api/state').json()
            new=[r for r in state["runs"] if r["id"] not in existing]
            if new:
                run=new[0]
                marker=(run['status'],tuple((j['stage'],j['status']) for j in state['jobs']))
                if marker!=last:
                    print(json.dumps({'run':run['id'],'status':run['status'],'jobs':state['jobs']},ensure_ascii=False),flush=True)
                    last=marker
                if run['status']!='running':
                    report['run']=run
                    break
            time.sleep(3)
        client.post('/api/actions/pause',json={}).raise_for_status()
        state=client.get('/api/state').json()
        report.update(active_documents=state['active_documents'],active_rules=state['active_rules'],candidate_count=len(state['candidates']),
            claims_count=sum(len(c['claims']) for c in state['candidates']),provider=state['provider'])
        run=report.get('run',{})
        trace_path=RUNTIME/'knowledge-demo/traces'/(run.get('id','missing')+'.json')
        if trace_path.exists():
            trace=json.loads(trace_path.read_text(encoding='utf-8'))
            events=trace['events']
            report['trace_sha256']=hashlib.sha256(trace_path.read_bytes()).hexdigest()
            report['tool_events']=[{'type':e.get('type'),'name':e.get('tool_name'),'depth':e.get('depth'),'agent_id':e.get('agent_id')} for e in events if e.get('type') in {'tool','subagent_start','subagent_end'}]
            report['runtime_errors']=[e.get('type') for e in events if e.get('type') in {'error','max_steps_reached'}]
            names=[e.get('tool_name') for e in events if e.get('type')=='tool']
            report['actual_tools']=names
            required={'govkb_classify_changes','govkb_submit_candidates','govkb_validate_candidates','govkb_finish_run'}
            starts=[e.get('agent_id') for e in events if e.get('type')=='subagent_start']
            ends=[e.get('agent_id') for e in events if e.get('type')=='subagent_end']
            report['child_call_ids']=starts
            report['children_completed_in_order']=starts==ends and native['extractor_id'] in starts and native['reviewer_id'] in starts
            report['passed']=run.get('status')=='completed' and required<=set(names) and report['children_completed_in_order'] and bool(state['active_documents']) and state['active_rules']==before['active_rules'] and report['claims_count']>0 and not report['runtime_errors']
    except Exception as error: report['error_type']=type(error).__name__
    finally:
        try: client.post('/api/actions/pause',json={})
        except httpx.HTTPError: pass
        client.close()
        directory=ROOT/'evidence/knowledge-heartbeat'
        directory.mkdir(parents=True,exist_ok=True)
        destination=directory/('native-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(destination.relative_to(ROOT))}),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

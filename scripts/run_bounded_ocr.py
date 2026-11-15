"""Dry plan by default. Live uploads require a bound user-approval file.

One new private run directory, no retries/resume, at most the approved four OCR
requests. Every attempt is reserved on disk before send. Original page mapping
and unreviewed/truncated status are kept; no QA calls or business-state writes.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gov_mcp.multimodal_evidence import Budget, UploadGrant, recognize, plan_fingerprint

PRIVATE=ROOT/'.runtime/dual-track-improvement-202611'


def execute(plan_path, approval_path, output, recognizer=recognize):
    plan_path,approval_path,output=Path(plan_path),Path(approval_path),Path(output)
    if not output.resolve().is_relative_to(PRIVATE.resolve()) or output.exists():
        raise ValueError('NEW_PRIVATE_RUN_DIRECTORY_REQUIRED_NO_RETRY')
    plan=json.loads(plan_path.read_text('utf-8'));approval=json.loads(approval_path.read_text('utf-8'))
    if (approval.get('confirmed_by_user') is not True or approval.get('public_non_sensitive_confirmed') is not True
            or not isinstance(approval.get('approval_reference'),str) or not approval['approval_reference'].strip()
            or approval.get('run_id')!=output.name or approval.get('plan_file_sha256')!=sha256(plan_path.read_bytes()).hexdigest()):
        raise PermissionError('EXPLICIT_BOUND_USER_APPROVAL_REQUIRED')
    items=plan.get('plans')
    if not isinstance(items,list) or len(items)!=4 or plan.get('max_qa_calls')!=0:
        raise ValueError('EXACT_FOUR_OCR_ONLY_PLAN_REQUIRED')
    if (type(approval.get('max_calls')) is not int or type(approval.get('max_pages')) is not int
            or type(approval.get('max_usd')) not in (int,float)
            or approval['max_calls']!=4 or approval['max_pages']!=4 or approval['max_usd']!=1):
        raise ValueError('APPROVED_FOUR_CALLS_FOUR_PAGES_USD_ONE_SCOPE_REQUIRED')
    checked=[]
    for item in items:
        core={k:v for k,v in item.items() if k not in ('image_path','plan_sha256_for_approval')}
        if (plan_fingerprint(core)!=item['plan_sha256_for_approval']
                or sha256(Path(item['image_path']).read_bytes()).hexdigest()!=core['upload_sha256']):
            raise ValueError('SOURCE_OR_PLAN_CHANGED_BEFORE_RUN')
        checked.append((item,core))
    if len({(core['upload_sha256'],core['purpose']) for _,core in checked})!=4:
        raise ValueError('DUPLICATED_OCR_TASK')
    output.mkdir()
    ledger={'approval_reference':approval['approval_reference'],'planned':4,'attempts':[],
            'qa_calls':0,'business_state_changes':0,'actual_account_charge_usd':None}
    budget=Budget(4,4,1,.01)
    ledger_path=output/'attempt-ledger.json'
    def save():
        temp=ledger_path.with_suffix('.tmp');temp.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),'utf-8');temp.replace(ledger_path)
    save()
    for index,(item,core) in enumerate(checked,1):
        attempt={'attempt':index,'plan_sha256':item['plan_sha256_for_approval'],'status':'reserved_before_send'}
        ledger['attempts'].append(attempt);save()
        grant=UploadGrant(approval['approval_reference'],core['upload_sha256'],core['purpose'],True,plan_fingerprint(core))
        try:
            pages=recognizer(Path(item['image_path']),core,grant,budget)
        except Exception:
            # Never record exception text or credentials. A timed-out request may be billed.
            attempt['status']='failed_or_uncertain_no_retry';save()
            break
        (output/f'candidate-pages-{index}.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),'utf-8')
        attempt.update(status='response_received_unreviewed',pages=len(pages),complete_pages=sum(p['complete'] for p in pages))
        save()
    return {'attempts':len(ledger['attempts']),'response_received':sum(a['status']=='response_received_unreviewed' for a in ledger['attempts']),
            'qa_calls':0,'human_reviewed':False,'actual_account_charge_usd':None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--approval-file',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if not args.live:
        print(json.dumps({'dry_run':True,'model_calls':0,'uploads':0,'approval_required':True,
                          'plan_file_sha256':sha256(args.plan.read_bytes()).hexdigest()},ensure_ascii=False));return
    if not args.approval_file or not args.output:
        parser.error('Explicit approval file and a new private run output are required')
    print(json.dumps(execute(args.plan,args.approval_file,args.output),ensure_ascii=False))


if __name__=='__main__':main()

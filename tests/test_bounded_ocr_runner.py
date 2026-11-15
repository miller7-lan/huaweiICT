"""Synthetic local approvals and recognizers only: real API/upload count is zero."""
from hashlib import sha256
import json

import pytest

import run_bounded_ocr as runner
from gov_mcp.multimodal_evidence import upload_plan,plan_fingerprint


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'private';root.mkdir();monkeypatch.setattr(runner,'PRIVATE',root)
    items=[]
    for page in (5,6):
        image=root/f'{page}.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+bytes([page]))
        for purpose in ('text_recognition','rich_layout_recognition'):
            core=upload_plan(image,'SYNTHETIC','https://example.invalid/source','a'*64,page,purpose)
            items.append({**core,'image_path':str(image),'plan_sha256_for_approval':plan_fingerprint(core)})
    plan=root/'plan.json';plan.write_text(json.dumps({'plans':items,'max_qa_calls':0}),'utf-8')
    output=root/'synthetic-run'
    grant={'confirmed_by_user':True,'public_non_sensitive_confirmed':True,'approval_reference':'synthetic fixture only',
           'run_id':output.name,'plan_file_sha256':sha256(plan.read_bytes()).hexdigest(),
           'max_calls':4,'max_pages':4,'max_usd':1}
    approval=root/'approval.json';approval.write_text(json.dumps(grant),'utf-8')
    return plan,approval,output


def test_approval_required_before_output_or_any_attempt(tmp_path,monkeypatch):
    plan,approval,output=fixture(tmp_path,monkeypatch)
    grant=json.loads(approval.read_text('utf-8'));grant['confirmed_by_user']=False;approval.write_text(json.dumps(grant),'utf-8')
    with pytest.raises(PermissionError):runner.execute(plan,approval,output)
    assert not output.exists()


def test_four_attempts_are_reserved_and_run_cannot_be_repeated(tmp_path,monkeypatch):
    plan,approval,output=fixture(tmp_path,monkeypatch)
    calls=[]
    def fake(path,core,grant,budget):
        ledger=json.loads((output/'attempt-ledger.json').read_text('utf-8'))
        assert ledger['attempts'][-1]['status']=='reserved_before_send'
        budget.reserve(1);calls.append(core);return [{'complete':False,'human_reviewed':False}]
    result=runner.execute(plan,approval,output,recognizer=fake)
    assert len(calls)==result['attempts']==result['response_received']==4
    assert result['qa_calls']==0 and not result['human_reviewed']
    with pytest.raises(ValueError,match='NO_RETRY'):runner.execute(plan,approval,output,recognizer=fake)
    assert len(calls)==4


def test_uncertain_failure_stops_remaining_requests_and_never_records_error_text(tmp_path,monkeypatch):
    plan,approval,output=fixture(tmp_path,monkeypatch)
    def failed(*args):raise RuntimeError('synthetic-secret')
    result=runner.execute(plan,approval,output,recognizer=failed)
    ledger=(output/'attempt-ledger.json').read_text('utf-8')
    assert result['attempts']==1 and result['response_received']==0
    assert 'synthetic-secret' not in ledger


def test_source_change_invalidates_grant_before_run(tmp_path,monkeypatch):
    plan,approval,output=fixture(tmp_path,monkeypatch)
    first=json.loads(plan.read_text('utf-8'))['plans'][0]
    from pathlib import Path
    Path(first['image_path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='SOURCE_OR_PLAN_CHANGED'):runner.execute(plan,approval,output)
    assert not output.exists()

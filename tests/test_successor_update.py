"""Bounded contract tests with synthetic fixtures, never real cloud evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from gov_mcp import successor_update as update
from gov_mcp.knowledge.store import KBError, digest

OLD = "一、压缩居民身份证办理时限。将居民身份证的办理时限（从受理之日起到领证之日）缩短至25个自然日。"
NEW = "一、压缩居民身份证办理时限。本省户籍居民申办居民身份证的时限（从受理之日起到领证之日）由25个自然日缩短至15个工作日；中考、高考特殊时期急需用证考生通过“绿色通道”申办居民身份证时限由15个自然日缩短至5个工作日。"


def docs():
    return {role:{"source_id":role,"url":"https://example.invalid/"+role,"revision":"synthetic",
                  "original_sha256":"1"*64,"page_publication_date":None,"effective_from":None,
                  "segments":[{"quote":text,"locator":"正文段落1"},{"quote":"政策解读：","locator":"正文段落2"},
                              {"quote":"合成测试解释，非业务复核","locator":"正文段落3"}]} for role,text in (("old",OLD),("new",NEW))}


def difference():
    return {"old":{"value":25,"unit":"calendar_day","source_id":"old","quote":OLD},
            "new":{"value":15,"unit":"working_day","source_id":"new","quote":NEW},
            "exception":{"value":5,"unit":"working_day","source_id":"new","quote":NEW},
            "population":"本省户籍居民","interval":"从受理之日起到领证之日",
            "exception_scope":"中考、高考特殊时期急需用证考生通过“绿色通道”",
            "relationship":"cross_url_successor_candidate","effective_date":None,
            "can_subtract_days":False,"whole_old_document_repealed":False}


@pytest.mark.parametrize("change",["unit","scope","date","quote","repeal","subtract","extra"])
def test_fresh_difference_must_preserve_source_boundaries(change):
    value=difference()
    if change=="unit": value["new"]["unit"]="calendar_day"
    elif change=="scope": value["exception_scope"]="所有急需用证人"
    elif change=="date": value["effective_date"]="2026-05-05"
    elif change=="quote": value["new"]["quote"]="15个工作日"
    elif change=="repeal": value["whole_old_document_repealed"]=True
    elif change=="subtract": value["can_subtract_days"]=True
    else: value["private_answer"]="not-allowed"
    with pytest.raises((ValueError, KBError)): update.validate_difference(value,docs())


@pytest.fixture
def engine(tmp_path,monkeypatch):
    monkeypatch.setattr(update,"load_documents",lambda root:deepcopy(docs()))
    (tmp_path/"arrival.json").write_text(json.dumps({"visible_roles":["old"],"enabled":True,"paused_reason":None}))
    return update.SuccessorEngine(tmp_path)


def leased(engine):
    run=engine.store.claim("synthetic-test",120,180)
    engine.poll(run)
    assert not engine.select_jobs(run)
    (engine.root/"arrival.json").write_text(json.dumps({"visible_roles":["old","new"],"enabled":True,"paused_reason":None}))
    engine.poll(run)
    assert len(engine.select_jobs(run))==1
    campaign={"run_id":run["id"],"binding":engine.snapshot()["binding"],"implementation":update.implementation()}
    engine.store.put("state","native_campaign",campaign)
    return run


def test_real_lifecycle_stops_at_pending_candidate_and_preserves_old_history(engine):
    run=leased(engine)
    with pytest.raises(KBError): engine.finish(run["id"])
    engine.get_batch(run["id"],"old");engine.get_batch(run["id"],"new")
    result=engine.submit(run["id"],difference())
    assert result["status"]=="pending_review"
    with pytest.raises(KBError): engine.submit(run["id"],difference())
    old_view=engine.get_batch(run["id"],"old");engine.get_batch(run["id"],"new")
    old_view=json.loads(old_view["packet"])
    assert old_view["document"]["segments"]==docs()["old"]["segments"]
    assert old_view["candidate_fields"]["new"]["value"]==15 and old_view["exact_complete_clause_quotes_checked"]
    engine.review(run["id"],{"acceptable":True,"omissions":[],"conflicts":[],"notes":["machine only"]})
    receipt=engine.finish(run["id"])
    assert receipt["candidate"]["formal_approval"] is False and receipt["published"] is False
    assert receipt["candidate"]["difference"]["old"]["quote"]==OLD
    assert engine.store.rows("versions")==[]


def test_pause_and_code_or_source_binding_change_block_children(engine):
    run=leased(engine)
    path=engine.root/"arrival.json"; saved=path.read_bytes()
    changed=json.loads(saved);changed["paused_reason"]="operator_pause";path.write_text(json.dumps(changed))
    with pytest.raises(KBError,match="SOURCE_PAUSED"): engine.get_batch(run["id"])
    path.write_bytes(saved)
    campaign=engine.store.get("state","native_campaign");campaign["binding"]="0"*64
    engine.store.put("state","native_campaign",campaign)
    with pytest.raises(KBError,match="BINDING_CHANGED"): engine.get_batch(run["id"])


def test_reviewer_cannot_approve_missing_scope_or_bypass_stage(engine):
    run=leased(engine)
    review={"acceptable":True,"omissions":[],"conflicts":[],"notes":[]}
    with pytest.raises(KBError,match="REVIEW_STAGE"):engine.review(run["id"],review)
    engine.get_batch(run["id"],"old");engine.get_batch(run["id"],"new")
    engine.submit(run["id"],difference())
    engine.get_batch(run["id"],"old");engine.get_batch(run["id"],"new")
    review["conflicts"]=["scope uncertain"]
    with pytest.raises(KBError,match="MACHINE_REVIEW"):engine.review(run["id"],review)
    assert engine.state(run["id"])["can_finish"] is False


def test_cloud_trace_checks_require_real_children_depth_order_and_no_retries():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    from check_successor_autonomy_cloud import native_trace_checks
    saved={"agents":{"extractor":31,"reviewer":32}}
    names=["read_skill_md","govkb_successor_state","govkb_successor_get","govkb_successor_get","govkb_successor_submit",
           "govkb_successor_state","govkb_successor_get","govkb_successor_get","govkb_successor_review","govkb_successor_state","govkb_successor_finish"]
    events=[{"type":"tool","tool_name":n,"depth":1 if n in {"govkb_successor_get","govkb_successor_submit","govkb_successor_review"} else 0} for n in names]
    events += [{"type":"subagent_start","agent_id":n,"depth":1} for n in (31,32)]
    events += [{"type":"model_attempt_control","phase":"begin","attempt":1}]
    assert native_trace_checks(events,saved)
    events[-1]["attempt"]=2
    assert not native_trace_checks(events,saved)
    saved["protocol_repair_enabled"]=True
    assert native_trace_checks(events,saved)
    events[-1]["attempt"]=4
    assert not native_trace_checks(events,saved)


def test_each_child_must_read_two_complete_views_once_before_submission(engine):
    run=leased(engine)
    with pytest.raises(KBError,match="BOTH_FULL"):engine.submit(run["id"],difference())
    with pytest.raises(KBError,match="OLD_THEN_NEW"):engine.get_batch(run["id"],"new")
    first=json.loads(engine.get_batch(run["id"],"old")["packet"])
    assert "difference_schema" not in first and len(first["document"]["segments"])==3
    with pytest.raises(KBError,match="REPEATED"):engine.get_batch(run["id"],"old")
    engine.get_batch(run["id"],"new")
    engine.submit(run["id"],difference())
    with pytest.raises(KBError,match="BOTH_FULL"):engine.review(run["id"],{"acceptable":True,"omissions":[],"conflicts":[],"notes":[]})


def test_evidence_packet_refuses_oversize_without_silently_cutting_policy():
    value={"full_clauses":[OLD,NEW]}
    packet=update.evidence_packet(value)
    assert json.loads(packet["packet"])==value
    with pytest.raises(KBError,match="EXCEEDS_PLATFORM_BUDGET"):update.evidence_packet({"full_clause":"x"*2100})


def test_native_child_limit_and_tool_error_are_immediate_stop_conditions():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    from check_successor_autonomy_cloud import native_event_failure
    assert native_event_failure({"type":"max_steps_reached","depth":1})=="NATIVE_CHILD_OR_CONTROLLER_FAILED"
    assert native_event_failure({"type":"execution_logs","content":'{"status":"blocked","error_code":"REPEATED_EVIDENCE_READ_BLOCKED"}'})=="TASK_TOOL_BLOCKED"
    assert native_event_failure({"type":"execution_logs","content":'complete source evidence'}) is None


def test_repair_cannot_refresh_drafts_after_campaign_has_executed(engine,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    import check_successor_autonomy_cloud as cloud
    monkeypatch.setattr(cloud,"RUNTIME",engine.root)
    monkeypatch.setattr(cloud,"check_drafts",lambda api,saved:None)
    path=engine.root/"native-drafts.json"
    path.write_text('{"agents":{}}',encoding="utf-8")
    original=path.read_bytes()
    engine.store.put("state","native_campaign",{"status":"failed","run_id":"historical"})
    with pytest.raises(KBError,match="EXECUTED_CAMPAIGN_DRAFTS_IMMUTABLE"):
        cloud.provision(None,refresh_owned=True)
    assert path.read_bytes()==original
    assert engine.store.get("state","native_campaign")["run_id"]=="historical"


def test_repair_campaign_rejects_path_traversal_before_preparing(monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    import check_successor_autonomy_cloud as cloud
    monkeypatch.setattr(sys,"argv",["check","prepare","--campaign","../../old"])
    with pytest.raises(ValueError,match="INVALID_REPAIR_CAMPAIGN"):cloud.main()


def test_exact_source_scope_may_retain_action_verb_without_dropping_conditions():
    value=difference();value["exception_scope"]+="申办"
    checked=update.validate_difference(value,docs())
    assert checked==value  # Preserve the actual model output; no silent rewrite.
    for scope in ("急需用证考生通过“绿色通道”申办","中考、高考特殊时期急需用证考生申办","所有人通过“绿色通道”申办"):
        invalid=deepcopy(value);invalid["exception_scope"]=scope
        with pytest.raises(KBError):update.validate_difference(invalid,docs())


def test_controller_template_executes_managed_children_only_after_persisted_gates():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    from check_successor_autonomy_cloud import controller_action,native_event_failure
    code=controller_action().replace('<code>','').replace('</code>','').replace('RUN_ID_LITERAL',repr('test-run'))
    for extraction_ok,review_ok in ((True,True),(False,True),(True,False)):
        calls=[];states=iter(['SUCCESSOR_STAGE:discovered','SUCCESSOR_STAGE:extracted' if extraction_ok else 'SUCCESSOR_STAGE:discovered',
                             'SUCCESSOR_STAGE:validated' if review_ok else 'SUCCESSOR_STAGE:extracted'])
        env={'read_skill_md':lambda **kw:calls.append('skill'),'govkb_successor_state':lambda **kw:next(states),
             'govsuccessor_extractor':lambda **kw:calls.append('extract'),'govsuccessor_reviewer':lambda **kw:calls.append('review'),
             'govkb_successor_finish':lambda **kw:(calls.append('finish') or {'published':False}),
             'final_answer':lambda value:calls.append(('final',value))}
        exec(code,env)
        assert ('review' in calls)==extraction_ok
        assert ('finish' in calls)==(extraction_ok and review_ok)
    assert native_event_failure({'type':'final_answer','content':'<DSML> unexecuted call'})=='NATIVE_UNEXECUTED_CALL_TEXT'


def test_native_state_wire_preserves_control_gates_without_json_imports():
    from gov_mcp.successor_update_server import state_wire
    for stage in ('discovered','extracted','validated','finished'):
        value={'stage':stage,'can_finish':stage=='validated','published':False}
        assert state_wire(value)=='SUCCESSOR_STAGE:'+stage
        invalid=dict(value,published=True)
        with pytest.raises(KBError):state_wire(invalid)
    blocked=state_wire({'status':'blocked','error_code':'SOURCE_PAUSED','published':False})
    assert 'SUCCESSOR_STAGE:' not in blocked
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
    from check_successor_autonomy_cloud import native_event_failure
    assert native_event_failure({'type':'warning','content':'Code execution failed: string has no attribute get'})=='NATIVE_CODE_EXECUTION_FAILED'

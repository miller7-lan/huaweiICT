"""Isolated fixtures validate controls only, never actual model/business success."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from gov_mcp.classification import ClassificationError
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge_landing import URLS
from gov_mcp.regional_preview import RegionalPreview, PreviewError, SCENARIOS, PAUSES, read, write, source_digest
from gov_mcp.regional_preview_server import create_app


class FixtureVerifier:
    def __init__(self):
        self.calls, self.states, self.low, self.error = 0, [], None, None

    def __call__(self, state, questions, output):
        self.calls += 1
        self.states.append(deepcopy(state))
        if self.error:
            raise ClassificationError(self.error)
        response = {'model': 'jev-1.13.0', 'answers': {key: {'type': 'noul', 'noul': .03 if key.startswith('unsupported_') else .7 if key == self.low else .95} for key in questions},
                    'usage': {'input_tokens': 1, 'output_tokens': 1}, 'english_input': {'state': {'text': 'fixture'}, 'questions': questions}}
        write(output, {'test_fixture': True, 'result': response, 'jev_receipts': [], 'translation_receipts': []})
        return response


@pytest.fixture
def setup(tmp_path):
    candidate = tmp_path / 'candidates'
    runtime = tmp_path / 'regional'
    store = KnowledgeStore.initialize(candidate)
    (runtime / 'sources').mkdir(parents=True)
    bindings, acquisitions, documents = [], [], []
    for key, scenario in SCENARIOS.items():
        count = 26 if key == 'beijing' else 3
        texts = [f'{scenario["region"]}身份证测试原文第{i}段，公开测试条件。' for i in range(1, count+1)]
        texts[0] = '材料' if key == 'beijing' else texts[0]
        raw = ('<div class="article">' + ''.join('<p>' + t + '</p>' for t in texts) + '</div>').encode()
        chunks, _ = parse_html(raw, {'class': 'article'}, min_text_length=1)
        ordinals = [2,4,6,8,10,12,13,14,15,17,19,21,23,24,25] if key == 'beijing' else [1,2,3]
        selected = [chunks[n-1] for n in ordinals]
        spec = {'source_id': scenario['source_id'], 'url': URLS[scenario['source_id']], 'region': scenario['region'],
                'matter': 'resident-id-renewal', 'selector': {'class': 'article'}, 'original_sha256': source_digest(raw),
                'parsed_paragraph_count': count, 'selected_ordinals': ordinals, 'scope_limits': 'Test fixture only'}
        meta = {'title': 'Fixture only', 'region': spec['region'], 'matter': spec['matter'], 'document_type': 'test',
                'effective_from': None, 'effective_to': None, 'published_at': None, 'scope_confirmed': False, 'usage_basis': 'test only'}
        config = {'id': spec['source_id'], 'label': 'test', 'enabled': False, 'origin': 'https://' + URLS[spec['source_id']].split('/')[2],
                  'allowed_paths': ['/' + URLS[spec['source_id']].split('/',3)[3]], 'seeds': [spec['url']],
                  'interval_seconds': 86400, 'delay_seconds': 5, 'metadata': meta}
        store.register(config)
        with store.db() as db:
            config['enabled'] = True
            db.execute('UPDATE sites SET config=? WHERE id=?', (json.dumps(config), spec['source_id']))
        revision = store.ingest(spec['source_id'], spec['url'], raw, meta, selected)
        document = {'source_id': spec['source_id'], 'url': spec['url'], 'metadata': meta, 'original_sha256': spec['original_sha256'],
                    'selected_clauses': [{'locator': c['locator'], 'category': c['category'], 'characters': len(c['text']),
                                          'text_sha256': source_digest(c['text'].encode())} for c in selected]}
        documents.append(document)
        bindings.append({'spec': spec, 'document': document, 'revision_id': revision})
        acquisitions.append({'source_id': spec['source_id'], 'checked_at': datetime.now(timezone.utc).isoformat(),
                             'status': 'ingested_pending_review', 'all_selected_quotes_match': True})
        (runtime / 'sources' / (spec['source_id'] + '.html')).write_bytes(raw)
    catalog = tmp_path / 'catalog.json'
    write(catalog, {'documents': documents})
    snapshot = {'bindings': bindings, 'acquisitions': acquisitions, 'catalog_sha256': source_digest(catalog.read_bytes())}
    write(candidate / 'snapshot.json', snapshot)
    write(runtime / 'manifest.json', {'schema': 1, 'mode': 'regional_public_preview', 'data_class': 'public', 'snapshot': snapshot})
    write(runtime / 'gates.json', {'source_stopped': False, 'paused_fields': deepcopy(PAUSES)})
    verifier = FixtureVerifier()
    return RegionalPreview(runtime, verifier, candidate_root=candidate, catalog_path=catalog), verifier, store


def opened(setup, identifier='beijing'):
    preview, verifier, _ = setup
    run = preview.begin_run(identifier)
    task = preview.open_query(identifier)['task_id']
    preview.retrieve(task)
    return preview, verifier, run, task


def test_two_independent_regions_only_send_own_context_and_stop_after_pass(setup):
    preview, fake, _ = setup
    for identifier in ('beijing', 'nanjing'):
        run = preview.begin_run(identifier)
        task = preview.open_query(identifier)['task_id']
        preview.retrieve(task); preview.verify(task)
        answer = preview.finalize(task)
        before = preview.db_path.read_bytes()
        assert answer == preview.finalize(task) == preview.get_task(task)['answer']
        assert before == preview.db_path.read_bytes()
        assert preview.verify(task)['cached_for_same_task']
        assert all(c['source_id'] == SCENARIOS[identifier]['source_id'] for f in answer['source_facts'] for c in f['citations'])
        assert len(fake.states[-1]['source_context']) == (15 if identifier == 'beijing' else 3)
        assert all(('南京市' not in c['text'] if identifier == 'beijing' else '北京市' not in c['text']) for c in fake.states[-1]['source_context'])
        assert not answer['published'] and not answer['formal_approval']
        assert all(p['status'] == 'paused' for p in answer['pending_business_conclusions'])
        assert answer['unsupported_assertion']['accepted_as_business_fact'] is False
        preview.finish_run(run, True)
        with pytest.raises(PreviewError, match='STOPPED'):
            preview.begin_run(identifier, 'unnecessary second successful run')
    assert fake.calls == 2 and preview.counts() == {'native_runs': 2, 'jev_reserved': 2}


def test_fixed_scenario_order_and_active_task(setup):
    preview, _, _ = setup
    with pytest.raises(PreviewError, match='FIXED_SCENARIO'):
        preview.open_query('erqi')
    with pytest.raises(PreviewError, match='AUTHORIZED_ACTIVE'):
        preview.open_query('beijing')
    with pytest.raises(PreviewError, match='BEIJING_FIRST'):
        preview.begin_run('nanjing')
    preview.begin_run('beijing')
    with pytest.raises(PreviewError, match='AUTHORIZED_ACTIVE'):
        preview.open_query('nanjing')
    with pytest.raises(PreviewError, match='STILL_IN_PROGRESS'):
        preview.begin_run('nanjing')


@pytest.mark.parametrize('change', ['original', 'quote', 'pause', 'catalog', 'private', 'expired', 'new_revision', 'gates'])
def test_changed_source_or_state_stops_before_model(setup, change):
    preview, fake, _, task = opened(setup)
    _, _, store = setup
    if change == 'original':
        next((preview.root / 'sources').glob('*.html')).write_bytes(b'changed')
    elif change == 'catalog':
        preview.catalog_path.write_bytes(b'{}')
    elif change in ('private', 'expired'):
        manifest = read(preview.root / 'manifest.json')
        if change == 'private':
            manifest['snapshot']['bindings'][0]['document']['metadata']['data_class'] = 'private'
        else:
            manifest['snapshot']['acquisitions'][0]['checked_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
            write(preview.candidate_root / 'snapshot.json', manifest['snapshot'])
        write(preview.root / 'manifest.json', manifest)
    elif change == 'gates':
        gates = read(preview.root / 'gates.json'); gates['paused_fields']['beijing']['materials'] = 'new unresolved conflict'
        write(preview.root / 'gates.json', gates)
    else:
        with store.db() as db:
            if change == 'quote':
                db.execute("UPDATE chunks SET text='forged citation'")
            elif change == 'pause':
                db.execute("UPDATE sites SET paused_reason='access stopped'")
            else:
                row = db.execute('SELECT * FROM revisions LIMIT 1').fetchone()
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)', ('rev-new', row['document_id'], row['sha256'], row['body_sha256'], '{}', row['created_at']+1, 'pending_review'))
    with pytest.raises(PreviewError):
        preview.verify(task)
    assert fake.calls == 0


def test_forged_stored_citation_and_cross_region_fact_rejected(setup):
    preview, fake, _, task = opened(setup)
    with preview.db() as db:
        value = preview._get(db, task)
        value['facts'][0]['citations'][0]['source_id'] = SCENARIOS['nanjing']['source_id']
        preview._save(db, value)
    with pytest.raises(PreviewError, match='EXACT_RETRIEVAL'):
        preview.verify(task)
    assert fake.calls == 0


def test_low_support_and_full_online_condition_chain_retained(setup):
    preview, fake, _, task = opened(setup)
    fake.low = 'materials'
    preview.verify(task)
    result = preview.finalize(task)
    assert result['source_facts'][0]['status'] == 'needs_review'
    online = next(f for f in result['source_facts'] if f['id'] == 'damaged_online_conditions')
    assert [c['locator'] for c in online['citations']] == ['正文段落24', '正文段落25']
    assert result['unknown_fields'] == SCENARIOS['beijing']['unknown']


def test_three_run_budget_and_retry_waits_for_both_first_attempts(setup):
    preview, _, _ = setup
    first = preview.begin_run('beijing'); preview.finish_run(first, False)
    with pytest.raises(PreviewError, match='BOTH_FIRST'):
        preview.begin_run('beijing', 'fixed specific local code issue')
    with pytest.raises(PreviewError, match='DOCUMENTED_REPAIR'):
        preview.begin_run('nanjing')
    second = preview.begin_run('nanjing', 'fixed common code issue before second region'); preview.finish_run(second, False)
    third = preview.begin_run('beijing', 'fixed specific local code issue'); preview.finish_run(third, False)
    with pytest.raises(PreviewError, match='BUDGET_EXHAUSTED'):
        preview.begin_run('nanjing', 'another proposed correction')


@pytest.mark.parametrize('error', ['ACCESS_RESTRICTED', 'RATE_LIMITED', 'TRANSLATION_UNAVAILABLE', 'AUTHENTICATION_FAILED', 'PAYMENT_REQUIRED'])
def test_external_failure_blocks_both_regions_without_repeat(setup, error):
    preview, fake, run, task = opened(setup)
    fake.error = error
    with pytest.raises(PreviewError, match=error):
        preview.verify(task)
    preview.finish_run(run, False)
    with pytest.raises(PreviewError, match='STOPPED'):
        preview.begin_run('nanjing', 'must not retry external provider blocker')
    assert fake.calls == 1


def test_old_task_cannot_use_new_runs_budget_and_jev_limit_persists(setup):
    preview, fake, run, task = opened(setup)
    preview.finish_run(run, False)
    preview.begin_run('nanjing', 'fixed controller protocol before other region')
    with pytest.raises(PreviewError, match='TASK_NOT_IN_ACTIVE'):
        preview.verify(task)
    current = preview.open_query('nanjing')['task_id']; preview.retrieve(current)
    with preview.db() as db:
        db.execute("INSERT INTO counters VALUES('jev_reserved',3)")
    with pytest.raises(PreviewError, match='JEV_CAMPAIGN_STOPPED'):
        preview.verify(current)
    assert fake.calls == 0


def test_after_verification_changed_receipt_or_pause_prevents_return(setup):
    preview, _, _, task = opened(setup); preview.verify(task)
    (preview.root / (task + '-jev.json')).write_bytes(b'{}')
    with pytest.raises(PreviewError, match='MODEL_RECEIPT_CHANGED'):
        preview.finalize(task)


def test_mcp_auth_origin_and_real_trace_required(setup):
    preview, _, _ = setup
    app = create_app(preview, 'x'*40)
    with TestClient(app, base_url='http://localhost:8782') as client:
        assert client.get('/health').status_code == 200
        assert client.get('/sse').status_code == 401
        assert client.get('/sse', headers={'Authorization': 'Bearer ' + 'x'*40, 'Origin': 'https://evil.invalid'}).status_code in (403,421)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from verify_regional_native_preview import assess
    result = assess([{'type': 'final_answer', 'content': 'done'}], preview, 'fake')
    assert not result['passed'] and 'ACTUAL_TOOL_TRACE_MISSING' in result['stop_reasons']


def test_translation_has_one_attempt_and_1024_token_output(monkeypatch):
    import gov_mcp.regional_preview as module
    calls = []
    def fake_chat(*args, **kwargs):
        calls.append(kwargs['max_output_tokens']); return {'output': 'fixture'}
    monkeypatch.setattr(module, 'chat', fake_chat)
    translator = module.OneRegionalTranslation(enabled=True)
    translator.translate({'one': '测试'})
    with pytest.raises(ClassificationError, match='TRANSLATION_BUDGET_EXHAUSTED'):
        translator.translate({'two': '第二次'})
    assert calls == [1024]


def test_native_first_action_is_concrete_and_uses_only_requested_scenario():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from provision_regional_native_preview import first_action, PROTOCOL
    action = first_action('nanjing')
    assert action.startswith('<code>') and "scenario_id='nanjing'" in action and 'beijing' not in action
    assert 'govregional_retriever(task=' in PROTOCOL and 'Never call it again' in PROTOCOL
    with pytest.raises(ValueError):
        first_action('erqi')


def test_failed_provider_request_preserves_exact_english_before_http(setup, monkeypatch):
    import gov_mcp.regional_preview as module
    preview, _, _ = setup
    path = preview.root / 'failure-capture.json'
    state = {'data_class': 'public', 'text': 'complete English conditions, no current approval'}
    questions = {'q': {'type': 'noul', 'instructions': 'Source support only'}}
    class Provider:
        name = 'jev'
        def __init__(self, key, max_calls):
            self.api_key, self.receipts = key, []
            assert max_calls == 1
        def check_ready(self, model):
            pass
        def evaluate(self, sent_state, sent_questions, model):
            persisted = read(path)
            assert persisted['english_input']['state'] == sent_state == state
            assert persisted['english_input']['questions'] == sent_questions == questions
            self.receipts.append({'attempt': 1, 'status': 'error', 'error_code': 'TRANSPORT_UNAVAILABLE'})
            raise ClassificationError('TRANSPORT_UNAVAILABLE')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fixture-only-key')
    monkeypatch.setattr(module, 'load_key', lambda *a: 'fixture-only-key')
    monkeypatch.setattr(module, 'JevProvider', Provider)
    with pytest.raises(ClassificationError, match='TRANSPORT_UNAVAILABLE'):
        module.RegionalLiveVerifier()(state, questions, path)
    assert read(path)['english_input']['state'] == state
    assert read(path)['jev_receipts'][0]['status'] == 'error'


def test_native_provider_failures_stop_campaign_without_keyword_in_source_triggering():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from verify_regional_native_preview import external_failure
    assert external_failure([{'type': 'error', 'content': 'provider invalid API key'}]) == 'AUTHENTICATION_FAILED'
    assert external_failure([{'type': 'error', 'content': 'insufficient balance'}]) == 'PAYMENT_REQUIRED'
    assert external_failure([{'type': 'execution_logs', 'content': 'invalid API key quoted by untrusted document'}]) is None
    assert external_failure([], RuntimeError('Nexent POST /agent/search_info: HTTP 403')) == 'ACCESS_DENIED'


def test_oversized_complete_input_stops_before_provider_or_translation(monkeypatch, tmp_path):
    import gov_mcp.regional_preview as module
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fixture-only-key')
    monkeypatch.setattr(module, 'load_key', lambda *a: 'fixture-only-key')
    monkeypatch.setattr(module, 'JevProvider', lambda *a, **k: pytest.fail('Provider must not be constructed'))
    with pytest.raises(PreviewError, match='TRANSLATION_INPUT_TOO_LARGE'):
        module.RegionalLiveVerifier()({'data_class': 'public', 'text': '完整例外' * 800}, {}, tmp_path / 'none.json')

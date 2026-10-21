"""Two fixed public regional previews, isolated from approved knowledge and Erqi."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from gov_mcp.classification import ClassificationError, JevProvider, validate_response
from gov_mcp.deepseek import PROFILES, load_key, chat
from gov_mcp.jev_translation import DeepSeekTranslator, EnglishJevProvider, PROMPT, VERSION, text_slots, translation_contract
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_landing import retrieve as candidate_retrieve
from gov_mcp.privacy import data_classes_allowed
from gov_mcp.public_preview import Preview, PreviewError, read, write, fingerprint, source_digest, utcnow

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / '.runtime/new-knowledge-experiment-20261018'
SERVICE = 'govkb_regional_preview'
SKILL = 'govkb-regional-preview'
TOOLS = {'govkb_regional_open_query', 'govkb_regional_retrieve', 'govkb_regional_verify',
         'govkb_regional_state', 'govkb_regional_finalize'}
SCENARIOS = {
    'beijing': {'source_id': 'BJ-ID-RENEWAL-2025', 'region': '北京市',
        'case': {'data_class': 'public', 'applicant_kind': 'fictional', 'age_group': 'adult',
                 'self_service': True, 'household_scope': 'local_beijing', 'reason': 'expiry'},
        'question': '成年、北京本市户籍、本人身份证期满换领：资料怎样记载材料、收费、时限及网办条件？请保留未成年人例外，区分损坏换领网办与期满换领。',
        'unknown': ['expiry_online_eligibility', 'current_applicability'],
        'negative_id': 'unsupported_expiry_online',
        'negative': 'Do the attached source texts explicitly establish that expiry renewal qualifies for the damaged-card online service?'},
    'nanjing': {'source_id': 'NJ-ID-RENEWAL-CYCLE-2026', 'region': '南京市',
        'case': {'data_class': 'public', 'applicant_kind': 'fictional', 'age_group': 'adult',
                 'self_service': True, 'household_scope': 'compare_in_province_and_outside_province', 'reason': 'expiry'},
        'question': '在南京办理身份证到期换证，省内、省外户籍的周期分别怎样记载？急需用证考生有什么服务，是否明确承诺3天？',
        'unknown': ['exam_expedited_days', 'current_applicability'],
        'negative_id': 'unsupported_exam_three_days',
        'negative': 'Do the attached source texts explicitly promise a three-day issuance deadline to candidates using the expedited service?'},
}
PAUSES = {
    'beijing': {'materials': '原文材料和监护条件尚未经当前业务复核。', 'duration': '10工作日为来源记载，现行执行及起止未核定。',
                'fees': '40/20元为来源记载，个人减免及现行收费未核定。', 'online_eligibility': '损坏换领网办限定不得外推期满换领资格。'},
    'nanjing': {'duration': '20工作日及省外增加5工作日为来源记载，现行执行未核定。',
                'exam_service': '考生快速服务无明确天数，具体适用及办理承诺待核。'},
}
EXTERNAL = {'CREDENTIAL_REQUIRED', 'MISSING_API_KEY', 'AUTHENTICATION_FAILED', 'PAYMENT_REQUIRED',
    'ACCESS_DENIED', 'ACCESS_RESTRICTED', 'RATE_LIMITED', 'TRANSPORT_UNAVAILABLE',
    'UPSTREAM_HTTP_ERROR', 'UPSTREAM_UNAVAILABLE', 'TRANSLATION_UNAVAILABLE'}


def scenario(identifier):
    if not isinstance(identifier, str) or identifier not in SCENARIOS:
        raise PreviewError('FIXED_SCENARIO_REQUIRED')
    return SCENARIOS[identifier]


def check_context(root, candidate_root=CANDIDATES, catalog_path=None):
    manifest = read(root / 'manifest.json')
    snapshot = read(candidate_root / 'snapshot.json')
    if (not data_classes_allowed(manifest, {'public'})
            or set(manifest) != {'schema', 'mode', 'data_class', 'snapshot'} or manifest['schema'] != 1
            or manifest['mode'] != 'regional_public_preview' or manifest['data_class'] != 'public'
            or fingerprint(manifest['snapshot']) != fingerprint(snapshot)):
        raise PreviewError('PINNED_PUBLIC_SNAPSHOT_REQUIRED')
    catalog_path = catalog_path or ROOT / 'data/knowledge/candidate-catalog.json'
    if source_digest(catalog_path.read_bytes()) != snapshot['catalog_sha256']:
        raise PreviewError('CATALOG_CHANGED')
    catalog = read(catalog_path)
    if len(snapshot['bindings']) != 2 or {b['spec']['source_id'] for b in snapshot['bindings']} != {s['source_id'] for s in SCENARIOS.values()}:
        raise PreviewError('FIXED_SOURCE_SET_REQUIRED')
    store = KnowledgeStore(candidate_root, read_only=True)
    contexts = {}
    for identifier, spec in SCENARIOS.items():
        binding = next(b for b in snapshot['bindings'] if b['spec']['source_id'] == spec['source_id'])
        document = next(d for d in catalog['documents'] if d['source_id'] == spec['source_id'] and d['url'] == binding['spec']['url'])
        if document != binding['document'] or document['metadata'].get('scope_confirmed') is not False:
            raise PreviewError('CATALOG_IDENTITY_CHANGED')
        acquisition = next(a for a in snapshot['acquisitions'] if a['source_id'] == spec['source_id'])
        when = datetime.fromisoformat(acquisition['checked_at'])
        if when.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-when).total_seconds() <= 86400:
            raise PreviewError('SOURCE_REFRESH_REQUIRED')
        if acquisition['status'] != 'ingested_pending_review' or acquisition.get('all_selected_quotes_match') is not True:
            raise PreviewError('VERIFIED_PUBLIC_ACQUISITION_REQUIRED')
        try:
            result = candidate_retrieve(store, snapshot['bindings'], spec['question'], spec['region'], 'resident-id-renewal')
        except (ValueError, OSError):
            raise PreviewError('CANDIDATE_CHANGED_OR_PAUSED') from None
        if len(result['source_context']) != 1 or result['source_context'][0]['source_id'] != spec['source_id']:
            raise PreviewError('REGION_ISOLATION_REQUIRED')
        context = result['source_context'][0]
        raw = (root / 'sources' / (spec['source_id'] + '.html')).read_bytes()
        if source_digest(raw) != context['original_sha256']:
            raise PreviewError('COPIED_ORIGINAL_CHANGED')
        contexts[identifier] = context
    gates = read(root / 'gates.json')
    if (set(gates) != {'source_stopped', 'paused_fields'} or gates['source_stopped'] is not False
            or set(gates['paused_fields']) != set(PAUSES)):
        raise PreviewError('CURRENT_PAUSE_GATE_BLOCKED')
    for key, required in PAUSES.items():
        if not set(required) <= gates['paused_fields'][key].keys() or any(
                not isinstance(v, str) or not v.strip() for v in gates['paused_fields'][key].values()):
            raise PreviewError('CURRENT_PAUSE_GATE_BLOCKED')
    return contexts, gates, fingerprint({'manifest': manifest, 'contexts': contexts, 'gates': gates})


def source_facts(identifier, context):
    def fact(key, ordinals, value, statement):
        references = []
        for ordinal in ordinals:
            chunk = next(c for c in context['selected_clauses'] if c['locator'] == f'正文段落{ordinal}')
            references.append({'source_id': context['source_id'], 'url': context['url'], 'locator': chunk['locator'],
                'quote': chunk['text'], 'start': 0, 'end': len(chunk['text']), 'raw_sha256': context['original_sha256']})
        return {'id': key, 'value': value, 'statement': statement, 'citations': references}
    if identifier == 'beijing':
        return [
            fact('materials', [4], 'original resident identity card; on-site service',
                 'The Beijing guide states that applicants bring the original resident identity card for on-site service. Applicants under sixteen must be accompanied by a guardian and provide the guardian original resident identity card.'),
            fact('duration', [10], '10 working days from acceptance',
                 'The Beijing guide states issuance within 10 working days from acceptance; it describes on-site fingerprint and portrait collection, a collection receipt, and return of the old card when collecting the new one.'),
            fact('fees', [19], {'damaged_card_cny': 40, 'other_reasons_cny': 20},
                 'The Beijing guide lists 40 CNY per card for damaged-card replacement and 20 CNY for replacement for other reasons. This does not establish personal fee relief or current applicability.'),
            fact('damaged_online_conditions', [24, 25], 'damaged-card branch only',
                 'The online note is limited to damaged-card replacement for local household registrants aged at least 16, who obtained a resident identity card within 5 years, had a resident identity card photograph collected within 5 years, and successfully registered at least one fingerprint. This does not establish expiry-renewal online eligibility.'),
        ]
    return [
        fact('duration', [2], {'in_province_working_days': 20, 'outside_province_additional_working_days': 5},
             'The Nanjing answer states a 20-working-day replacement cycle for in-province household registrants. For outside-province registrants, review and issuance by their household-registration police authority adds 5 working days.'),
        fact('exam_service', [3], 'expedited service without a stated day count',
             'For candidates urgently needing identity documents, the Nanjing answer describes expedited acceptance, review, production and issuance to shorten the cycle. It does not state a specific number of days.'),
    ]


class OneRegionalTranslation(DeepSeekTranslator):
    def translate(self, texts):
        if self.attempts >= 1:
            raise ClassificationError('TRANSLATION_BUDGET_EXHAUSTED')
        self.attempts += 1
        try:
            return chat(PROFILES['official'], [{'role': 'system', 'content': PROMPT},
                {'role': 'user', 'content': json.dumps(texts, ensure_ascii=False, separators=(',', ':'))}],
                max_output_tokens=1024, response_model=translation_contract(texts))
        except Exception:
            raise ClassificationError('TRANSLATION_UNAVAILABLE') from None


class RegionalLiveVerifier:
    def __call__(self, state, questions, output):
        import os
        key = os.environ.get('TYPESAFE_API_KEY', '') or read(ROOT / '.runtime/jev/official.credentials.json').get('TYPESAFE_API_KEY', '')
        if not load_key(PROFILES['official']) or not isinstance(key, str) or not key.strip():
            raise PreviewError('CREDENTIAL_REQUIRED')
        if sum(len(t) for t in {slot[2] for slot in text_slots(deepcopy(state), deepcopy(questions))}) > 3000:
            raise PreviewError('TRANSLATION_INPUT_TOO_LARGE')
        raw = JevProvider(key, max_calls=1)
        adapter = EnglishJevProvider(raw, OneRegionalTranslation(enabled=True))
        record = {'original_state': state, 'original_questions': questions, 'result': None}
        request = raw.evaluate

        def persist_before_http(english_state, english_questions, model):
            record['english_input'] = {'version': VERSION, 'state': deepcopy(english_state), 'questions': deepcopy(english_questions)}
            # Persist before sending: a transport failure must not discard the
            # translated request. Credentials never enter these payloads.
            write(output, record)
            return request(english_state, english_questions, model)

        raw.evaluate = persist_before_http
        try:
            record['result'] = validate_response(adapter.evaluate(state, questions, 'jev-1.13.0'), questions, 'jev-1.13.0')
            return record['result']
        except ClassificationError:
            if raw.receipts:
                code = {401: 'AUTHENTICATION_FAILED', 402: 'PAYMENT_REQUIRED', 403: 'ACCESS_DENIED',
                        429: 'RATE_LIMITED', 451: 'ACCESS_RESTRICTED'}.get(raw.receipts[-1].get('http_status'))
                if code:
                    raise PreviewError(code) from None
            raise
        finally:
            record.update(jev_receipts=raw.receipts, translation_receipts=adapter.translation_receipts)
            write(output, record)


class RegionalPreview(Preview):
    def __init__(self, root, verifier=None, *, candidate_root=CANDIDATES, catalog_path=None):
        self.candidate_root, self.catalog_path = candidate_root, catalog_path
        super().__init__(root, verifier)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scenario_runs(run_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL)')

    def _context(self, task=None):
        contexts, gates, binding = check_context(self.root, self.candidate_root, self.catalog_path)
        if task and task['binding'] != binding:
            raise PreviewError('SNAPSHOT_OR_PAUSE_CHANGED')
        return contexts, gates, binding

    def begin_run(self, scenario_id, repair_note=None):
        scenario(scenario_id); self._context()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            runs = db.execute('SELECT r.*,s.scenario_id FROM runs r JOIN scenario_runs s ON s.run_id=r.id ORDER BY ordinal').fetchall()
            if db.execute("SELECT 1 FROM flags WHERE name='external_block' OR name=?", ('passed:' + scenario_id,)).fetchone():
                raise PreviewError('CAMPAIGN_OR_SCENARIO_STOPPED')
            if len(runs) >= 3:
                raise PreviewError('NATIVE_RUN_BUDGET_EXHAUSTED')
            if any(r['status'] == 'running' for r in runs):
                raise PreviewError('NATIVE_RUN_STILL_IN_PROGRESS')
            if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload,'$.status')='verifying'").fetchone():
                raise PreviewError('VERIFICATION_STILL_IN_PROGRESS')
            if not runs and scenario_id != 'beijing':
                raise PreviewError('BEIJING_FIRST_REQUIRED')
            previous = [r for r in runs if r['scenario_id'] == scenario_id]
            if previous and {r['scenario_id'] for r in runs} != set(SCENARIOS):
                raise PreviewError('BOTH_FIRST_ATTEMPTS_REQUIRED')
            if (runs and runs[-1]['status'] == 'failed') or previous:
                if not isinstance(repair_note, str) or not 8 <= len(repair_note) <= 300:
                    raise PreviewError('DOCUMENTED_REPAIR_REQUIRED')
            run_id = uuid.uuid4().hex
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (run_id, len(runs)+1, 'running', repair_note, utcnow()))
            db.execute('INSERT INTO scenario_runs VALUES(?,?)', (run_id, scenario_id))
            db.execute("INSERT OR REPLACE INTO flags VALUES('active_run',?)", (run_id,))
        return run_id

    def finish_run(self, run_id, passed):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT scenario_id FROM scenario_runs WHERE run_id=?', (run_id,)).fetchone()
            if not row:
                raise PreviewError('RUN_NOT_FOUND')
            db.execute('UPDATE runs SET status=? WHERE id=?', ('passed' if passed else 'failed', run_id))
            if passed:
                db.execute('INSERT OR REPLACE INTO flags VALUES(?,?)', ('passed:' + row[0], 'true'))

    def open_query(self, scenario_id):
        scenario(scenario_id)
        _, _, binding = self._context()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT value FROM flags WHERE name='active_run'").fetchone()
            run = active and db.execute("SELECT r.id,s.scenario_id FROM runs r JOIN scenario_runs s ON s.run_id=r.id WHERE r.id=? AND r.status='running'", (active[0],)).fetchone()
            if not run or run['scenario_id'] != scenario_id:
                raise PreviewError('AUTHORIZED_ACTIVE_SCENARIO_REQUIRED')
            existing = db.execute('SELECT payload FROM tasks WHERE run_id=?', (run['id'],)).fetchone()
            if existing:
                task = json.loads(existing[0]); self._context(task)
            else:
                task = {'task_id': uuid.uuid4().hex, 'run_id': run['id'], 'scenario_id': scenario_id, 'binding': binding,
                        'status': 'opened', 'data_class': 'public', 'facts': None, 'verification': None, 'answer': None}
                self._save(db, task)
        return {'task_id': task['task_id'], 'status': task['status'], 'scenario_id': scenario_id, 'mode': 'regional_public_preview'}

    def retrieve(self, task_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._get(db, task_id); contexts, _, _ = self._context(task)
            expected = source_facts(task['scenario_id'], contexts[task['scenario_id']])
            if task['facts'] is not None and task['facts'] != expected:
                raise PreviewError('STORED_CITATION_CHANGED')
            if task['status'] == 'opened':
                task.update(facts=expected, status='retrieved'); self._save(db, task)
            return {'task_id': task_id, 'scenario_id': task['scenario_id'], 'status': task['status'],
                    'source_facts': task['facts'], 'source_context': contexts[task['scenario_id']]}

    def verify(self, task_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._get(db, task_id); contexts, _, _ = self._context(task)
            identifier = task['scenario_id']; spec = scenario(identifier)
            if task['facts'] != source_facts(identifier, contexts[identifier]):
                raise PreviewError('EXACT_RETRIEVAL_REQUIRED')
            if task['verification'] is not None:
                return {'task_id': task_id, 'status': task['status'], 'verification': task['verification'], 'cached_for_same_task': True}
            active = db.execute("SELECT value FROM flags WHERE name='active_run'").fetchone()
            run = db.execute('SELECT status FROM runs WHERE id=?', (task['run_id'],)).fetchone()
            if not active or active[0] != task['run_id'] or not run or run[0] != 'running':
                raise PreviewError('TASK_NOT_IN_ACTIVE_RUN')
            if task['status'] != 'retrieved' or self.verifier is None:
                raise PreviewError('LIVE_VERIFICATION_NOT_READY')
            count = db.execute("SELECT value FROM counters WHERE name='jev_reserved'").fetchone()
            if (count and count[0] >= 3) or db.execute("SELECT 1 FROM flags WHERE name='external_block'").fetchone():
                raise PreviewError('JEV_CAMPAIGN_STOPPED')
            db.execute("INSERT OR REPLACE INTO counters VALUES('jev_reserved',?)", ((count[0] if count else 0)+1,))
            task['status'] = 'verifying'; self._save(db, task)
        state = {'data_class': 'public', 'case': spec['case'], 'claims': [
            {'id': f['id'], 'value': f['value'], 'statement': f['statement'],
             'source_text': '\n'.join(c['quote'] for c in f['citations'])} for f in task['facts']],
            'source_context': [{'locator': c['locator'], 'text': c['text']} for c in contexts[identifier]['selected_clauses']]}
        questions = {f['id']: {'type': 'noul', 'instructions': 'Does claim ' + f['id'] + ' accurately describe its own source_text with the attached conditions? Judge source support only, not current applicability.'} for f in task['facts']}
        questions[spec['negative_id']] = {'type': 'noul', 'instructions': spec['negative']}
        try:
            response = validate_response(self.verifier(state, questions, self.root / (task_id + '-jev.json')), questions, 'jev-1.13.0')
            if not isinstance(response.get('english_input'), dict):
                raise PreviewError('ENGLISH_INPUT_RECEIPT_REQUIRED')
            receipt_sha = source_digest((self.root / (task_id + '-jev.json')).read_bytes())
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                task = self._get(db, task_id); self._context(task)
                active = db.execute("SELECT value FROM flags WHERE name='active_run'").fetchone()
                run = db.execute('SELECT status FROM runs WHERE id=?', (task['run_id'],)).fetchone()
                if not active or active[0] != task['run_id'] or not run or run[0] != 'running':
                    raise PreviewError('TASK_NOT_IN_ACTIVE_RUN')
                task.update(status='can_finalize', verification={'probabilities': {k: v['noul'] for k, v in response['answers'].items()},
                    'threshold': .8, 'receipt_sha256': receipt_sha, 'source_binding': task['binding']})
                self._save(db, task)
            return {'task_id': task_id, 'status': 'can_finalize', 'verification': task['verification']}
        except Exception as error:
            code = str(error) if isinstance(error, (PreviewError, ClassificationError)) and re.fullmatch('[A-Z_]+', str(error)) else 'VERIFICATION_UNAVAILABLE'
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                task = self._get(db, task_id); task.update(status='failed', error_code=code); self._save(db, task)
                if code in EXTERNAL:
                    db.execute("INSERT OR REPLACE INTO flags VALUES('external_block',?)", (code,))
            raise PreviewError(code) from None

    def state(self, task_id):
        with self.db() as db:
            task = self._get(db, task_id); self._context(task)
            return {'task_id': task_id, 'scenario_id': task['scenario_id'], 'status': task['status'], 'mode': 'regional_public_preview'}

    def finalize(self, task_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._get(db, task_id); contexts, gates, _ = self._context(task)
            identifier = task['scenario_id']; spec = scenario(identifier)
            if task['status'] not in {'can_finalize', 'complete'} or task['facts'] != source_facts(identifier, contexts[identifier]):
                raise PreviewError('VERIFIED_TASK_REQUIRED')
            verification = task['verification']; path = self.root / (task_id + '-jev.json')
            if source_digest(path.read_bytes()) != verification['receipt_sha256'] or verification['source_binding'] != task['binding']:
                raise PreviewError('MODEL_RECEIPT_CHANGED')
            probabilities = {k: v['noul'] for k, v in read(path)['result']['answers'].items()}
            if probabilities != verification['probabilities'] or verification['threshold'] != .8:
                raise PreviewError('STORED_VERDICT_CHANGED')
            facts = [{**f, 'status': 'source_supported' if probabilities[f['id']] >= .8 else 'needs_review', 'support': probabilities[f['id']]} for f in task['facts']]
            answer = {'task_id': task_id, 'scenario_id': identifier, 'status': 'partial_preview', 'mode': 'regional_public_preview',
                'data_class': 'public', 'snapshot_binding': task['binding'], 'source_facts': facts,
                'source_context': contexts[identifier], 'unknown_fields': spec['unknown'],
                'pending_business_conclusions': [{'field': k, 'status': 'paused', 'reason': v} for k, v in gates['paused_fields'][identifier].items()],
                'unsupported_assertion': {'id': spec['negative_id'], 'support': probabilities[spec['negative_id']],
                    'status': 'rejected' if probabilities[spec['negative_id']] <= .2 else 'needs_review', 'accepted_as_business_fact': False},
                'published': False, 'formal_approval': False, 'notice': '真实公开资料原文预览，现行业务适用性待核，未提交申请。'}
            if task['answer'] is not None:
                if task['answer'] != answer:
                    raise PreviewError('PERSISTED_ANSWER_CHANGED')
                return task['answer']
            task.update(status='complete', answer=answer); self._save(db, task)
            return answer

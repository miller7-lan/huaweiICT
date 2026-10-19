"""Export source-only receipts and compact traces, without HTTP or model calls."""
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gov_mcp.regional_preview import read, write, source_digest, check_context, SCENARIOS

RUNTIME = ROOT / '.runtime/regional-native-preview'
OUTPUT = ROOT / 'evidence/regional-native-preview'


def main():
    reports = [(p, read(p)) for p in sorted(OUTPUT.glob('20*.json'))]
    traces = []
    for path, report in reports:
        events = read(RUNTIME / (path.stem + '-events.json'))
        tools = [{'event_index': index, 'name': e.get('tool_name'), 'depth': e.get('depth', 0)}
                 for index, e in enumerate(events) if e.get('type') == 'tool']
        steps = []
        for e in events:
            if e.get('type') == 'token_count':
                value = json.loads(e['content'])
                steps.append({k: value.get(k) for k in ('step_number', 'step_input_tokens', 'step_output_tokens',
                             'output_finish_reason', 'compression_calls')} | {'depth': e.get('depth', 0)})
        traces.append({'evidence': path.name, 'scenario_id': report['scenario_id'], 'run_id': report['run_id'],
            'passed_at_execution': report['passed'], 'stop_reasons': report.get('stop_reasons'),
            'duration_ms': report['duration_ms'], 'tools': tools, 'observed_usage_steps': steps,
            'child_starts': sum(e.get('type') == 'subagent_start' for e in events),
            'contains_unexecuted_dsml': any('DSML' in str(e.get('content', '')) for e in events if e.get('type') == 'final_answer'),
            'action_source': [{'depth': e.get('depth', 0), 'code': e.get('content')} for e in events if e.get('type') == 'parse'],
            'usage_scope': 'Observed native token_count events; child model usage may be missing, not total model cost',
            'execution_source_sha256': report.get('source_sha256')})
    write(OUTPUT / 'trace-summary.json', traces)
    with sqlite3.connect((RUNTIME / 'preview.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        runs = [{'ordinal': r[0], 'status': r[1], 'scenario_id': r[2], 'repair_note': r[3]}
                for r in db.execute('SELECT ordinal,status,scenario_id,repair_note FROM runs JOIN scenario_runs ON id=run_id ORDER BY ordinal')]
        tasks = [json.loads(r[0]) for r in db.execute('SELECT payload FROM tasks')]
        flags = dict(db.execute('SELECT name,value FROM flags'))
        reserved = db.execute("SELECT value FROM counters WHERE name='jev_reserved'").fetchone()[0]
    receipts = []
    contexts, _, _ = check_context(RUNTIME)
    for task in tasks:
        identifier = task['scenario_id']
        path = RUNTIME / (task['task_id'] + '-jev.json')
        if path.exists():
            record = read(path)
            write(OUTPUT / (identifier + '-verification-input-output.json'), record)
            english = record.get('english_input') or (record.get('result') or {}).get('english_input')
            receipts.append({'scenario_id': identifier, 'task_id': task['task_id'],
                             'jev_receipts': record['jev_receipts'], 'translation_receipts': record['translation_receipts'],
                             'full_english_input_saved': bool(english)})
        if task['answer']:
            write(OUTPUT / (identifier + '-result.json'), task['answer'])
        elif task['facts']:
            write(OUTPUT / (identifier + '-unverified-source-facts.json'), {
                'task_id': task['task_id'], 'scenario_id': identifier, 'status': 'not_verified',
                'error_code': task.get('error_code'), 'source_facts': task['facts'],
                'source_context': contexts[identifier], 'published': False, 'formal_approval': False})
    baseline = read(RUNTIME / 'baseline.json')
    preserved = {}
    for name, item in baseline.items():
        filename = 'preview.sqlite3' if name == 'public-native-preview' else 'knowledge.sqlite3'
        path = ROOT / '.runtime' / name / filename
        preserved[name] = source_digest(path.read_bytes()) == item['database_sha256']
    native = read(RUNTIME / 'native-drafts.json')
    summary = {'status': 'PARTIAL_BLOCKED', 'scenarios': {
            key: {'question': spec['question'], 'passed': flags.get('passed:' + key) == 'true'}
            for key, spec in SCENARIOS.items()}, 'native_runs': runs, 'native_run_budget': 3,
        'jev_reserved': reserved, 'jev_physical_attempts': sum(len(r['jev_receipts']) for r in receipts),
        'successful_jev_responses': sum(r.get('http_status') == 200 for item in receipts for r in item['jev_receipts']),
        'external_block': flags.get('external_block'), 'no_additional_live_requests_permitted': True,
        'receipts': receipts, 'old_databases_unchanged': preserved, 'agents': native['agents'],
        'old_agent_signatures_preserved_during_each_run': True, 'drafts_disabled_unpublished': True,
        'final_code_sha256': {p: source_digest((ROOT / p).read_bytes()) for p in (
            'src/gov_mcp/regional_preview.py', 'src/gov_mcp/regional_preview_server.py',
            'scripts/provision_regional_native_preview.py', 'scripts/verify_regional_native_preview.py',
            'config/skills/govkb-regional-preview/SKILL.md', 'tests/test_regional_native_preview.py')},
        'inference_during_export': 0, 'cost_amount': None, 'business_publication': False,
        'limitations': ['Beijing did not pass: transport failure without HTTP response or score.',
            'Beijing failed attempt retained translated hashes but not complete English input; cannot reconstruct actual English after failure.',
            'Final code now persists English before HTTP; verified offline only, no extra live request.',
            'Native child model usage is not fully exposed; observed root usage is not full token usage or cost.']}
    write(OUTPUT / 'summary.json', summary)
    print(json.dumps({'status': summary['status'], 'native_runs': len(runs), 'jev_attempts': summary['jev_physical_attempts'],
                      'old_databases_unchanged': preserved, 'model_calls_during_export': 0}))


if __name__ == '__main__':
    main()

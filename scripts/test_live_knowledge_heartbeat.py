"""Bounded natural-clock heartbeat and official candidate update test, no models."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import Fetcher, AccessStopped, schedule, worker_once
from gov_mcp.knowledge_lock import process_lock
from gov_mcp.public_preview import read, write, source_digest
from private_runtime import ensure_private_directory
from run_new_knowledge_experiment import configured_proxy_request
from check_project_heartbeat import inspect

RUNTIME = ROOT / '.runtime/heartbeat-update-experiment-20261018'
OUTPUT = ROOT / 'evidence/heartbeat-update-experiment'
SOURCE = 'NJ-ID-RENEWAL-CYCLE-2026'
OWNER = 'knowledge-live-heartbeat-test'


class BoundedOfficialFetcher(Fetcher):
    def __init__(self, store):
        super().__init__(store)
        self.requests = []

    def _request(self, url, headers):
        # This transport only allows the two existing fixed official hosts and
        # paths. Fetcher's source policy narrows this test to Nanjing alone.
        if len(self.requests) >= 4:
            raise AccessStopped('Bounded experiment HTTP budget exhausted')
        record = {'at': datetime.now(timezone.utc).isoformat(), 'url': url}
        self.requests.append(record)
        try:
            status, response_headers, raw = configured_proxy_request(url, headers)
            record.update(http_status=status, bytes=len(raw), sha256=source_digest(raw),
                          conditional_request=bool(headers))
            if url.endswith('/robots.txt'):
                (RUNTIME / ('robots-' + str(len(self.requests)) + '.txt')).write_bytes(raw)
            return status, response_headers, raw
        except Exception as error:
            record.update(error_type=type(error).__name__)
            raise AccessStopped('Live transport failed; no automatic retry') from None


def configure(store):
    snapshot = read(ROOT / '.runtime/new-knowledge-experiment-20261018/snapshot.json')
    binding = next(b for b in snapshot['bindings'] if b['spec']['source_id'] == SOURCE)
    spec = binding['spec']
    basis = 'Official public text; private bounded engineering update test; no business review or publication'
    config = {'id': SOURCE, 'label': 'Nanjing official renewal cycle bounded update test', 'enabled': True,
        'origin': 'https://www.nanjing.gov.cn', 'allowed_paths': ['/hdjl/hygq/202601/t20260105_5757179.html'],
        'seeds': [spec['url']], 'interval_seconds': 60, 'delay_seconds': 5, 'discover_links': False,
        'content_selector': spec['selector'], 'metadata': dict(binding['document']['metadata'],
            effective_from=None, effective_to=None, usage_basis=basis, scope_confirmed=False),
        'access_review': {'approved': True, 'reviewer': 'codex_automated_technical_access_screen',
            'actor_kind': 'machine', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'usage_basis': basis, 'business_review_completed': False, 'purpose': 'bounded_private_engineering_test'}}
    store.register(config)
    return binding


def observe(store, started):
    with store.db() as db:
        worker = db.execute('SELECT heartbeat,progress FROM workers WHERE id=?', (OWNER,)).fetchone()
        counts = {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in ('documents', 'revisions', 'chunks', 'reviews', 'versions', 'heads')}
        jobs = [dict(r) for r in db.execute('SELECT id,state,attempts,error FROM jobs')]
        source = dict(db.execute('SELECT last_success,next_check,paused_reason FROM sites WHERE id=?', (SOURCE,)).fetchone())
    return {'elapsed_seconds': round(time.monotonic()-started, 3), 'observed_at': time.time(),
            'worker': dict(worker) if worker else None, 'counts': counts, 'jobs': jobs, 'source': source}


def resume_due_check():
    prior = read(OUTPUT / 'live-result.json')
    marker = RUNTIME / 'timing-repair.json'
    failed = {key for key, value in prior['checks'].items() if not value}
    if (marker.exists() or failed != {'two_scheduled_official_checks'} or len(prior['requests']) != 2
            or any(r.get('http_status') != 200 for r in prior['requests'])):
        raise ValueError('Repair only allowed once for an early tick after successful HTTP; no remote failure retry')
    store = KnowledgeStore(RUNTIME)
    binding = next(b for b in read(ROOT / '.runtime/new-knowledge-experiment-20261018/snapshot.json')['bindings']
                   if b['spec']['source_id'] == SOURCE)
    with process_lock(RUNTIME / 'worker.lock') as acquired:
        if not acquired:
            raise ValueError('Existing worker preserved')
        with store.db() as db:
            source = db.execute('SELECT paused_reason,next_check FROM sites WHERE id=?', (SOURCE,)).fetchone()
        if source['paused_reason'] != 'Bounded experiment completed; no recurring task registered':
            raise ValueError('External or operator pause must not be cleared')
        delay = source['next_check']-time.time()+0.02
        if delay > 60:
            raise ValueError('Unexpected future due time; no forced scheduling')
        write(OUTPUT / 'before-timing-repair.json', prior)
        write(marker, {'reason': 'Tick was 5.5ms before persisted next_check; wait for the real due time',
                       'http_budget_total': 4, 'time_or_due_mutations': False})
        fetcher = BoundedOfficialFetcher(store)
        fetcher.requests = prior['requests']
        observations, snapshots = prior['observations'], prior['existing_service_heartbeats']
        try:
            if delay > 0:
                time.sleep(delay)
            with store.db() as db:
                db.execute('UPDATE sites SET paused_reason=NULL WHERE id=?', (SOURCE,))
            schedule(store)
            worker_once(store, fetcher, owner=OWNER)
            observation = observe(store, time.monotonic())
            observation['elapsed_seconds'] = round(observation['observed_at']-observations[0]['observed_at']+observations[0]['elapsed_seconds'], 3)
            observation['timing_repair'] = True
            observations.append(observation)
            snapshots.append(inspect())
        finally:
            with store.db() as db:
                db.execute("UPDATE sites SET paused_reason=COALESCE(paused_reason,'Bounded experiment completed; no recurring task registered') WHERE id=?", (SOURCE,))
                db.execute("UPDATE workers SET progress='completed' WHERE id=?", (OWNER,))
                store.log(db, 'bounded_test_stopped', {'source': SOURCE, 'http_attempts': len(fetcher.requests)})
        return finish_report(store, binding, fetcher, observations, snapshots)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume-due-check', action='store_true')
    args = parser.parse_args()
    if args.live == args.resume_due_check:
        parser.error('Choose initial --live or the once-only diagnosed --resume-due-check')
    if args.resume_due_check:
        return resume_due_check()
    if RUNTIME.exists() and any(RUNTIME.iterdir()):
        raise ValueError('Prior experiment preserved; no automatic reset or repeated live test')
    store = KnowledgeStore.initialize(RUNTIME)
    ensure_private_directory(RUNTIME)
    binding = configure(store)
    fetcher = BoundedOfficialFetcher(store)
    observations, snapshots = [], []
    with process_lock(RUNTIME / 'worker.lock') as acquired:
        if not acquired:
            raise ValueError('Another bounded test owns this runtime')
        started = time.monotonic()
        try:
            for tick in range(3):
                remaining = started + tick*30 - time.monotonic()
                if tick == 2:
                    with store.db() as db:
                        due = db.execute('SELECT next_check FROM sites WHERE id=?', (SOURCE,)).fetchone()[0]
                    remaining = max(remaining, due-time.time()+0.02)
                if remaining > 0:
                    time.sleep(remaining)
                # No fabricated clock, forced next_check or direct ingestion.
                schedule(store)
                worker_once(store, fetcher, owner=OWNER)
                observations.append(observe(store, started))
                snapshots.append(inspect())
                write(RUNTIME / 'observations.json', observations)
                print(json.dumps({'tick': tick+1, 'elapsed': observations[-1]['elapsed_seconds'],
                                  'counts': observations[-1]['counts'], 'jobs': observations[-1]['jobs']}), flush=True)
                if any(job['error'] for job in observations[-1]['jobs']):
                    break
        finally:
            with store.db() as db:
                db.execute("UPDATE sites SET paused_reason=COALESCE(paused_reason,'Bounded experiment completed; no recurring task registered') WHERE id=?", (SOURCE,))
                db.execute("UPDATE workers SET progress='completed' WHERE id=?", (OWNER,))
                store.log(db, 'bounded_test_stopped', {'source': SOURCE, 'http_attempts': len(fetcher.requests)})
        return finish_report(store, binding, fetcher, observations, snapshots)


def finish_report(store, binding, fetcher, observations, snapshots):
    with store.db() as db:
        audit = [{'at': row['at'], 'action': row['action'], 'data': json.loads(row['data'])}
                 for row in db.execute('SELECT * FROM audit ORDER BY id')]
        latest = db.execute('SELECT id FROM revisions ORDER BY rowid DESC LIMIT 1').fetchone()
        verified = None
        if latest:
            row, _ = store._verify(db, latest[0], require_review=False)
            _, chunks = store._content(db, row['id'])
            expected = binding['document']['selected_clauses']
            verified = {'raw_matches_shared_catalog': row['sha256'] == binding['spec']['original_sha256'],
                'all_quotes_match': len(chunks) == len(expected) and all(
                    c['locator'] == e['locator'] and source_digest(c['text'].encode()) == e['text_sha256'] for c,e in zip(chunks,expected)),
                'revision_state': row['state']}
    success = [a for a in audit if a['action'] == 'check_succeeded']
    checks = {'three_real_clock_heartbeats': len(observations) >= 3 and observations[-1]['elapsed_seconds'] >= 60,
        'worker_heartbeat_advances': len(observations) >= 3 and observations[-1]['worker']['heartbeat'] > observations[0]['worker']['heartbeat'],
        'two_scheduled_official_checks': len(success) == 2,
        'unchanged_source_no_duplicate_revision': len(observations) >= 3 and observations[0]['counts']['revisions'] == observations[-1]['counts']['revisions'] == 1,
        'official_original_and_quotes_match': bool(verified and verified['raw_matches_shared_catalog'] and verified['all_quotes_match']),
        'candidate_only': bool(verified and verified['revision_state'] == 'pending_review') and
            all(o['counts']['reviews'] == o['counts']['versions'] == o['counts']['heads'] == 0 for o in observations)}
    result = {'status': 'PASS_BOUNDED_UPDATE' if all(checks.values()) else 'INCOMPLETE', 'checks': checks,
        'observations': observations, 'existing_service_heartbeats': snapshots, 'requests': fetcher.requests,
        'candidate_verification': verified, 'audit': audit, 'model_calls': 0, 'permanent_schedule_created': False,
        'test_source_paused_after_completion': True,
        'limits': 'About one minute of natural-clock candidate acquisition/recheck; no observed official policy change, no formal publication or long-term uptime claim'}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / 'live-result.json', result)
    print(json.dumps({'status': result['status'], 'checks': checks, 'http_attempts': len(fetcher.requests)}), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())

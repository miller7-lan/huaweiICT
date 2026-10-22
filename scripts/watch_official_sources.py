"""Observe one approved official source using existing acquisition and candidate storage.

No model, review or publication path. Every external request is durably reserved.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import AccessStopped, BOT, Fetcher, MAX_BYTES, schedule, worker_once
from gov_mcp.knowledge_lock import process_lock
from private_runtime import ensure_private_directory

RUNTIME = ROOT / '.runtime/official-source-watch'
SOURCE = 'NJ-ID-RENEWAL-CYCLE-2026'
URL = 'https://www.nanjing.gov.cn/hdjl/hygq/202601/t20260105_5757179.html'
INTERVAL = 21600
HTTP_DAILY_LIMIT = 12


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WatchFetcher(Fetcher):
    def _request(self, url, headers):
        if url not in {URL, 'https://www.nanjing.gov.cn/robots.txt'}:
            raise AccessStopped('Outside fixed official source allowlist')
        now = time.time()
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute("SELECT COUNT(*) FROM audit WHERE action='watch_http_reserved' AND at>=?", (midnight,)).fetchone()[0]
            if count >= HTTP_DAILY_LIMIT:
                raise AccessStopped('Daily official HTTP budget exhausted; review before resume')
            self.store.log(db, 'watch_http_reserved', {'url':url, 'at':now})
        try:
            proxies = urllib.request.getproxies()
            proxy = proxies.get('https') or proxies.get('http')
            if proxy:
                # Fixed HTTPS destination; proxy resolves the official hostname.
                # The direct branch retains the existing public-IP/TLS checks.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy}), NoRedirect(),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context()))
                req = urllib.request.Request(url, headers={'User-Agent':BOT+'/0.1 (public-document research)',
                    'Accept-Encoding':'identity', **headers})
                try: response = opener.open(req, timeout=20)
                except urllib.error.HTTPError as error: response = error
                with response:
                    raw = response.read(MAX_BYTES+1)
                    status, result_headers = response.status, {k.lower():v for k,v in response.headers.items()}
                    if len(raw)>MAX_BYTES: raise AccessStopped('Response too large')
            else:
                status, result_headers, raw = super()._request(url, headers)
            with self.store.db() as db:
                self.store.log(db, 'watch_http_result', {'url':url,'http_status':status,'bytes':len(raw),
                    'sha256':hashlib.sha256(raw).hexdigest(),'conditional':bool(headers), 'uses_configured_proxy':bool(proxy)})
            allowed = {200,404,410} if urlsplit(url).path == '/robots.txt' else {200,304}
            if status not in allowed:
                raise AccessStopped('Official HTTP stopped; no automatic retry', http_status=status)
            return status, result_headers, raw
        except AccessStopped: raise
        except (OSError, ValueError) as error:
            # Never expose proxy credentials, URLs from exceptions or response text.
            raise AccessStopped('Official transport stopped: '+type(error).__name__) from None


def prepare():
    if (RUNTIME/'watch-config.json').exists():
        return KnowledgeStore(RUNTIME)
    store = KnowledgeStore.initialize(RUNTIME)
    ensure_private_directory(RUNTIME)
    catalog = json.loads((ROOT/'data/knowledge/candidate-catalog.json').read_text('utf-8'))
    document = next(d for d in catalog['documents'] if d['source_id']==SOURCE and d['url']==URL)
    basis = 'Official public source observation; engineering candidates only; no formal business approval'
    config = {'id':SOURCE,'label':'南京官方换证周期来源观察','enabled':True,'origin':'https://www.nanjing.gov.cn',
        'allowed_paths':[urlsplit(URL).path],'seeds':[URL],'interval_seconds':INTERVAL,'delay_seconds':5,
        'discover_links':False,'content_selector':{'id':'con'},
        'metadata':dict(document['metadata'], effective_from=None, effective_to=None, scope_confirmed=False,
            usage_basis=basis, business_review='pending'),
        'access_review':{'approved':True,'reviewer':'codex_automated_technical_access_screen','actor_kind':'machine',
            'checked_at':datetime.now(timezone.utc).isoformat(),'usage_basis':basis,'business_review_completed':False}}
    store.register(config)
    marker = {'schema_version':1,'source_id':SOURCE,'config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
        'mode':'observation_only','interval_seconds':INTERVAL,'daily_http_limit':HTTP_DAILY_LIMIT,'model_calls':0}
    (RUNTIME/'watch-config.json').write_text(json.dumps(marker,indent=2),encoding='utf-8')
    return store


def status(store):
    with store.db() as db:
        sources = [dict(r) for r in db.execute('SELECT id,last_success,next_check,paused_reason FROM sites')]
        counts = {t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('documents','revisions','chunks','reviews','versions','heads')}
        requests = [json.loads(r[0]) for r in db.execute("SELECT data FROM audit WHERE action='watch_http_result' ORDER BY id")]
        reserved = db.execute("SELECT COUNT(*) FROM audit WHERE action='watch_http_reserved'").fetchone()[0]
        jobs = [dict(r) for r in db.execute('SELECT id,state,attempts,error FROM jobs')]
        workers = [dict(r) for r in db.execute('SELECT * FROM workers')]
    return {'mode':'observation_only','sources':sources,'counts':counts,'http_reservations':reserved,
        'requests':requests,'jobs':jobs,'workers':workers,'model_calls':0,'automatic_publication':False}


def cycle(store):
    marker = json.loads((store.root/'watch-config.json').read_text('utf-8'))
    if marker.get('mode')!='observation_only' or marker.get('source_id')!=SOURCE:
        raise ValueError('WATCH_MODE_CHANGED')
    with process_lock(store.root/'source-watch.lock') as acquired:
        if not acquired: return {'action':'already_checking','model_calls':0}
        with store.db() as db:
            rows = db.execute('SELECT config FROM sites').fetchall()
        if len(rows)!=1 or hashlib.sha256(json.dumps(json.loads(rows[0][0]),sort_keys=True).encode()).hexdigest()!=marker['config_sha256']:
            raise ValueError('WATCH_APPROVED_SOURCE_CONFIG_CHANGED')
        if not (store.root/'watch-paused.json').exists():
            schedule(store)
            worker_once(store, fetcher=WatchFetcher(store), owner='official-source-watch')
        return status(store)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','cycle','status','pause','resume'))
    args = parser.parse_args()
    if args.action=='prepare': store=prepare()
    else: store=KnowledgeStore(RUNTIME)
    if args.action=='pause': (store.root/'watch-paused.json').write_text('{"paused":true}',encoding='utf-8')
    if args.action=='resume': (store.root/'watch-paused.json').unlink(missing_ok=True)
    result = cycle(store) if args.action=='cycle' else status(store)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__': main()

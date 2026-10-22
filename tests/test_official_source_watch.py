import importlib.util
import json
from pathlib import Path

import pytest

from gov_mcp.knowledge_fetch import AccessStopped

spec=importlib.util.spec_from_file_location('watch_official_sources',Path(__file__).parents[1]/'scripts/watch_official_sources.py')
watch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(watch,'RUNTIME',tmp_path/'watch')
    return watch.prepare()


def test_prepare_cannot_reset_persisted_due_time_or_approval(store):
    with store.db() as db: db.execute('UPDATE sites SET next_check=9999999999')
    marker=(store.root/'watch-config.json').read_bytes()
    second=watch.prepare()
    assert watch.status(second)['sources'][0]['next_check']==9999999999
    assert (store.root/'watch-config.json').read_bytes()==marker


def test_manual_pause_prevents_scheduling_and_changed_config_blocks_network(store,monkeypatch):
    def forbidden(*a,**kw): pytest.fail('No HTTP allowed')
    monkeypatch.setattr(watch.WatchFetcher,'_request',forbidden)
    (store.root/'watch-paused.json').write_text('{}')
    assert watch.cycle(store)['jobs']==[]
    (store.root/'watch-paused.json').unlink()
    with store.db() as db:
        config=json.loads(db.execute('SELECT config FROM sites').fetchone()[0]);config['seeds']=['https://127.0.0.1/']
        db.execute('UPDATE sites SET config=?',(json.dumps(config),))
    with pytest.raises(ValueError,match='APPROVED_SOURCE_CONFIG_CHANGED'):watch.cycle(store)


def test_transport_failure_pauses_source_and_never_retries_on_next_cycle(store,monkeypatch):
    calls=[]
    def stopped(*a,**kw):
        calls.append(1);raise AccessStopped('Configured transport unavailable')
    monkeypatch.setattr(watch.WatchFetcher,'_request',stopped)
    first=watch.cycle(store);second=watch.cycle(store)
    assert len(calls)==1 and first['sources'][0]['paused_reason']
    assert second['jobs'][0]['attempts']==1 and second['jobs'][0]['state']=='failed'
    assert second['counts']['versions']==second['counts']['heads']==0


def test_request_budget_survives_new_fetcher_and_unknown_urls_consume_nothing(store):
    with pytest.raises(AccessStopped,match='allowlist'):watch.WatchFetcher(store)._request('https://127.0.0.1/',{})
    assert watch.status(store)['http_reservations']==0
    with store.db() as db:
        for _ in range(watch.HTTP_DAILY_LIMIT):store.log(db,'watch_http_reserved',{})
    with pytest.raises(AccessStopped,match='budget exhausted'):watch.WatchFetcher(store)._request(watch.URL,{})
    assert watch.status(store)['http_reservations']==watch.HTTP_DAILY_LIMIT


def test_proxy_transport_reserves_before_io_and_sanitizes_exception(store,monkeypatch):
    monkeypatch.setattr(watch.urllib.request,'getproxies',lambda:{'https':'http://proxy.fixture:8080'})
    class Opener:
        def open(self,*a,**kw):
            assert watch.status(store)['http_reservations']==1
            raise OSError('proxy://user:private-password@bad-host')
    monkeypatch.setattr(watch.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(AccessStopped) as caught:watch.WatchFetcher(store)._request(watch.URL,{})
    assert 'private-password' not in str(caught.value)
    assert 'private-password' not in json.dumps(watch.status(store))


def test_passive_inspection_reports_watcher_without_mutating_private_database(store,tmp_path):
    from check_project_heartbeat import inspect_source_watch
    # The inspection accepts a repository root, with its fixed private subdirectory.
    target=tmp_path/'.runtime/official-source-watch'
    target.parent.mkdir()
    store.root.rename(target)
    path=target/'knowledge.sqlite3'
    before=path.read_bytes()
    value=inspect_source_watch(tmp_path)
    assert value['sources'][0]['id']==watch.SOURCE
    assert value['counts']['versions']==0 and not value['model_dispatch_enabled']
    assert path.read_bytes()==before

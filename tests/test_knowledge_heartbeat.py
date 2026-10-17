from copy import deepcopy
import json
from pathlib import Path
import time

import httpx
import pytest
from starlette.testclient import TestClient

from gov_mcp.knowledge.engine import Engine
from gov_mcp.knowledge.replay import SyntheticReplay
from gov_mcp.knowledge.server import create_app
from gov_mcp.knowledge.sources import BodyParser, Fetcher
from gov_mcp.knowledge.store import KBError, Store
from gov_mcp.knowledge.worker import Worker

ROOT = Path(__file__).parents[1]


class ReplayDriver:
    """Exercise maintenance operations, not a claim of native/model verification."""
    def __init__(self): self.calls = 0
    def run(self, engine, run):
        self.calls += 1
        for key in run["jobs"]:
            engine.classify(run["id"], key)
            _, job = engine.context(run["id"], key)
            if not job["candidate_id"]:
                s = job["assets"][0]["segments"][0]
                name = "申请表" if "申请表" in s["content"] else s["content"][:2]
                candidate = engine.submit(run["id"], key, [{"kind":"Material","name":name,"quote":s["content"],"source_segment_ids":[s["segment_id"]]}])
            else: candidate = engine.store.get("candidates", job["candidate_id"])
            engine.validate(run["id"], candidate["id"], {"omissions":[],"conflicts":[],"notes":[]})
            engine.finish_job(run["id"], key, "工程回放，等待人工复核。")
    def stop(self, run): pass


@pytest.fixture
def setup(tmp_path):
    config = json.loads((ROOT/"config/knowledge/heartbeat.json").read_text(encoding="utf-8"))
    source = {"id":"test-source","title":"模拟指南","url":"http://fixture.test/policy","format":"html",
        "body_selector":{"tag":"article","id":"policy"},"allowed_hosts":["fixture.test"],"attachment_urls":[],
        "data_class":"synthetic","enabled":True,"approved":True}
    config["sources"] = [source]
    classifier = json.loads((ROOT/"config/preparation/classification.json").read_text(encoding="utf-8"))
    web = {"body":'<nav>导航</nav><article id="policy"><p>模拟事项需提交申请表。</p></article>',"status":200,"calls":0}
    def handle(request):
        web["calls"] += 1
        return httpx.Response(web["status"], text=web["body"], headers={"content-type":"text/html"})
    store = Store(tmp_path)
    fetcher = Fetcher(store, client=httpx.Client(transport=httpx.MockTransport(handle)), synthetic=True)
    engine = Engine(store, config, classifier, provider=SyntheticReplay(), fetcher=fetcher, embedder=lambda texts:[[1.,0.] for _ in texts], synthetic=True)
    return engine, web, ReplayDriver()


def round_now(engine, driver, force=True):
    run = engine.store.claim("test-worker", 120, 600)
    assert run
    Worker(engine, driver).round(run, force)
    return engine.store.get("runs", run["id"])


def test_full_flow_separates_automatic_documents_and_human_rules(setup):
    engine, web, driver = setup
    run = round_now(engine, driver)
    assert run["status"] == "completed" and driver.calls == 1
    documents = engine.store.get("state","active_documents")
    assert documents and engine.store.get("state","active_rules") is None
    result = engine.search("申请表")
    assert result["items"][0]["content"] == "模拟事项需提交申请表。"
    assert engine.search("申请表","rules")["status"] == "EMPTY"
    candidate = engine.store.rows("candidates")[0]
    with pytest.raises(KBError,match="HUMAN_CONFIRMATION"):
        engine.review_candidate(candidate["id"],None,"human")
    rules = engine.review_candidate(candidate["id"],None,"human",acknowledge=True)
    assert engine.search("申请表","rules")["items"]
    assert engine.store.version(rules)["kind"] == "rules"
    assert engine.store.version(documents)["data"]["assets"][0]["review_status"] == "machine_candidate"


def test_no_change_and_navigation_noise_do_not_call_models_or_republish(setup):
    engine, web, driver = setup
    round_now(engine,driver)
    before = engine.store.get("state","active_documents")
    web["body"] = web["body"].replace("导航", "导航和访问量12345")
    round_now(engine,driver)
    assert driver.calls == 1 and engine.store.get("state","active_documents") == before
    assert len(engine.store.rows("jobs")) == 1
    assert engine.store.rows("sources")[0]["status"] == "unchanged"


def test_changed_body_selector_forces_reparse_instead_of_reusing_304(setup):
    engine, web, driver = setup
    observed = []
    def handle(request):
        observed.append(dict(request.headers))
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        return httpx.Response(200, text='<article id="policy">模拟甲申请表。</article><article id="new">模拟乙演练材料。</article>',
                              headers={"content-type":"text/html", "etag":'"stable"'})
    engine.fetcher.client = httpx.Client(transport=httpx.MockTransport(handle))
    round_now(engine, driver)
    engine.config["sources"][0]["body_selector"]["id"] = "new"
    round_now(engine, driver)
    assert "if-none-match" not in observed[-1]
    assert engine.search("演练材料")["items"]
    assert driver.calls == 2


def test_withdrawn_source_approval_prevents_recovery_publication(setup):
    engine, web, driver = setup
    run = engine.store.claim("before-crash",120,600)
    engine.poll(run,True)
    engine.select_jobs(run)
    driver.run(engine,run)
    assert engine.needs_publication()
    engine.config["sources"][0]["approved"] = False
    assert not engine.needs_publication()
    assert engine.publish_documents(run) is None
    assert engine.store.get("state","active_documents") is None
    engine.store.finish(run,"completed")


def withdraw_source(engine, change):
    if change == "remove": engine.config["sources"] = []
    elif change == "config": engine.config["sources"][0]["body_selector"]["id"] = "changed"
    else: engine.config["sources"][0][change] = False


@pytest.mark.parametrize("change", ["approved", "enabled", "remove", "config"])
def test_source_revocation_blocks_pending_rule_acceptance_but_not_rejection(setup, change):
    engine, _, driver = setup
    round_now(engine, driver)
    candidate = engine.store.rows("candidates")[0]
    withdraw_source(engine, change)
    with pytest.raises(KBError, match="^SOURCE_NOT_CURRENT$"):
        engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    assert engine.store.get("state", "active_rules") is None
    assert engine.store.get("candidates", candidate["id"])["status"] == "pending_review"
    engine.review_candidate(candidate["id"], None, "human", accept=False)
    assert engine.store.get("candidates", candidate["id"])["status"] == "rejected"


@pytest.mark.parametrize("change", ["approved", "enabled", "remove", "config"])
def test_source_revocation_withholds_current_evidence_but_keeps_explicit_history(setup, change):
    engine, _, driver = setup
    round_now(engine, driver)
    candidate = engine.store.rows("candidates")[0]
    rules = engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    documents = engine.store.get("state", "active_documents")
    withdraw_source(engine, change)
    engine.embedder = lambda texts: pytest.fail("Embedding revoked current documents")
    for kind in ("documents", "rules"):
        result = engine.search("申请表", kind)
        assert not result["items"] and not result["historical"] and "授权撤销" in result["notice"]
    engine.embedder = lambda texts: [[1., 0.] for _ in texts]
    for kind, version in (("documents", documents), ("rules", rules)):
        result = engine.search("申请表", kind, version)
        assert result["items"] and result["historical"] and "历史快照" in result["notice"]


@pytest.mark.parametrize("change", ["approved", "enabled", "remove", "config"])
def test_in_flight_source_revocation_blocks_model_and_evidence_stages(setup, change):
    engine, _, driver = setup
    run = engine.store.claim("owner", 120, 600)
    engine.poll(run, True)
    key = engine.select_jobs(run)[0]
    driver.run(engine, run)
    candidate = engine.store.rows("candidates")[0]
    withdraw_source(engine, change)
    class Forbidden:
        name = "forbidden"
        def evaluate(self, *args): pytest.fail("Revoked source reached model")
    engine.provider = Forbidden()
    for action in (lambda: engine.context(run["id"], key), lambda: engine.classify(run["id"], key),
                   lambda: engine.validate(run["id"], candidate["id"], {"omissions": [], "conflicts": [], "notes": []})):
        with pytest.raises(KBError, match="^SOURCE_CHANGED_DURING_RUN$"): action()


def test_failed_reconfigured_fetch_cannot_reauthorize_stale_pending_group(setup):
    engine, web, driver = setup
    round_now(engine, driver)
    candidate = engine.store.rows("candidates")[0]
    engine.config["sources"][0]["url"] = "http://fixture.test/replacement"
    web["status"] = 503
    round_now(engine, driver)
    assert not engine.source_can_publish(engine.store.rows("sources")[0])
    with pytest.raises(KBError, match="^SOURCE_NOT_CURRENT$"):
        engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    assert not engine.search("申请表")["items"]


def test_successful_reconfiguration_does_not_reauthorize_older_same_body_rules(setup):
    engine, _, driver = setup
    round_now(engine, driver)
    candidate = engine.store.rows("candidates")[0]
    rules = engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    engine.config["sources"][0]["title"] = "新授权范围"
    round_now(engine, driver)
    assert engine.search("申请表")["items"]
    assert not engine.search("申请表", "rules")["items"]
    assert engine.search("申请表", "rules", rules)["items"]


def test_transient_fetch_failure_preserves_approved_last_known_good(setup):
    engine, web, driver = setup
    round_now(engine, driver)
    candidate = engine.store.rows("candidates")[0]
    engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    web["status"] = 503
    round_now(engine, driver)
    assert engine.search("申请表")["items"] and engine.search("申请表", "rules")["items"]


def test_legacy_document_version_authority_can_be_proven_without_new_snapshot_map(setup):
    engine, _, driver = setup
    round_now(engine, driver)
    current = engine.store.get("state", "active_documents")
    data = deepcopy(engine.store.version(current)["data"])
    data.pop("source_configs")
    run = engine.store.claim("legacy-fixture", 120, 600)
    engine.store.publish("documents", data, current, "legacy-fixture", run)
    engine.store.finish(run, "completed")
    assert engine.search("申请表")["items"]
    engine.config["sources"][0]["body_selector"]["id"] = "changed"
    assert not engine.search("申请表")["items"]


def test_accepted_ontology_change_keeps_current_claims_and_documents_searchable(setup):
    engine, _, driver = setup
    run = engine.store.claim("owner", 120, 600)
    engine.poll(run, True)
    key = engine.select_jobs(run)[0]
    engine.classify(run["id"], key)
    _, job = engine.context(run["id"], key)
    segment = job["assets"][0]["segments"][0]
    candidate = engine.submit(run["id"], key,
        [{"kind": "Material", "name": "申请表", "quote": segment["content"], "source_segment_ids": [segment["segment_id"]]}],
        [{"type": "entity_type", "name": "ServiceChannel", "description": "人工确认的服务渠道类型"}])
    engine.validate(run["id"], candidate["id"], {"omissions": [], "conflicts": [], "notes": []})
    engine.finish_job(run["id"], key, "等待人工确认。")
    engine.publish_documents(run)
    engine.store.finish(run, "completed")
    before = engine.processing_hash
    engine.review_candidate(candidate["id"], None, "human", acknowledge=True)
    assert engine.processing_hash != before
    assert engine.search("申请表", "rules")["items"] and engine.search("申请表")["items"]
    round_now(engine, driver)
    assert engine.search("申请表", "rules")["items"]


@pytest.mark.parametrize("withdrawn", [True, False])
def test_embedding_migration_excludes_revoked_cached_sources_and_retains_authorized_sources(setup, withdrawn):
    engine, web, driver = setup
    web["body"] = '<article id="policy">来源甲申请表。</article>'
    round_now(engine, driver)
    previous = engine.store.get("state", "active_documents")
    second = deepcopy(engine.config["sources"][0])
    second.update(id="source-b", url="http://fixture.test/b")
    engine.config["sources"] = [second] if withdrawn else engine.config["sources"]+[second]
    engine.config["models"]["embedding"]["model"] = "new-fixture-model"
    def handle(request):
        if request.url.path == "/policy": return httpx.Response(503)
        return httpx.Response(200, text='<article id="policy">来源乙申请表。</article>', headers={"content-type": "text/html"})
    engine.fetcher.client = httpx.Client(transport=httpx.MockTransport(handle))
    calls = []
    def embed(texts):
        calls.append(texts)
        return [[1., 0., 0.] for _ in texts]
    engine.embedder = embed
    round_now(engine, driver)
    assert any("来源乙" in text for texts in calls for text in texts)
    assert any("来源甲" in text for texts in calls for text in texts) is not withdrawn
    current = engine.store.version(engine.store.get("state", "active_documents"))["data"]
    assert ("test-source" in current["source_configs"]) is not withdrawn
    assert all(len(vector) == 3 for vector in current["vectors"].values())
    assert engine.store.version(previous)["data"]["assets"][0]["segments"][0]["content"] == "来源甲申请表。"


def test_legacy_unchanged_source_keeps_authority_when_unrelated_source_changes_processing(setup):
    engine, _, driver = setup
    round_now(engine, driver)
    previous = engine.store.get("state", "active_documents")
    legacy = deepcopy(engine.store.version(previous)["data"])
    legacy.pop("source_configs")
    run = engine.store.claim("legacy-fixture", 120, 600)
    engine.store.publish("documents", legacy, previous, "legacy-fixture", run)
    engine.store.finish(run, "completed")
    second = deepcopy(engine.config["sources"][0])
    second.update(id="source-b", url="http://fixture.test/b")
    engine.config["sources"].append(second)
    engine.config["models"]["embedding"]["model"] = "new-fixture-model"
    def handle(request):
        if request.url.path == "/policy": return httpx.Response(503)
        return httpx.Response(200, text='<article id="policy">模拟乙申请表。</article>', headers={"content-type": "text/html"})
    engine.fetcher.client = httpx.Client(transport=httpx.MockTransport(handle))
    round_now(engine, driver)
    current = engine.store.version(engine.store.get("state", "active_documents"))["data"]
    assert current["processing_hash"] != legacy["processing_hash"]
    assert "test-source" in current["source_configs"]
    assert any(item["source_id"] == "test-source" for item in engine.search("申请表")["items"])


def test_expired_source_fetch_cannot_enqueue(setup):
    engine, web, driver = setup
    now = [1000.0]
    engine.store.clock = lambda: now[0]
    run = engine.store.claim("expiring",120,600)
    def expired_fetch(*args, **kwargs):
        now[0] += 121
        return {"body_sha256":"changed", "assets":[{"asset_id":"must-not-enqueue"}]}
    engine.fetcher.fetch = expired_fetch
    with pytest.raises(KBError,match="STALE_EXECUTOR"):
        engine.poll(run,True)
    assert engine.store.rows("jobs") == []
    assert engine.store.rows("sources") == []


def test_interrupted_original_write_does_not_leave_partial_content_object(setup,monkeypatch):
    engine, web, driver = setup
    import os
    replace = os.replace
    monkeypatch.setattr(os,"replace",lambda *args: (_ for _ in ()).throw(OSError("simulated interruption")))
    with pytest.raises(OSError):
        engine.store.object(b"complete original")
    assert list((engine.store.root/"objects").iterdir()) == []
    monkeypatch.setattr(os,"replace",replace)
    key = engine.store.object(b"complete original")
    assert engine.store.read_object(key) == b"complete original"


@pytest.mark.parametrize("status,body,code", [(403,"禁止访问","SOURCE_HTTP_403"),(200,"<article>空匹配</article>","BODY_NOT_FOUND"),
    (200,'<article id="policy">请输入验证码</article>',"CHALLENGE_PAGE")])
def test_failed_fetch_keeps_old_version_and_never_reports_unchanged(setup,status,body,code):
    engine,web,driver=setup
    round_now(engine,driver)
    before=engine.store.get("state","active_documents")
    web.update(status=status,body=body)
    round_now(engine,driver)
    source=engine.store.rows("sources")[0]
    assert source["status"] == "failed" and source["error"] == code
    assert engine.store.get("state","active_documents") == before and driver.calls == 1


def test_missing_key_skips_native_and_preserves_queue(setup):
    from gov_mcp.classification import JevProvider
    engine,web,driver=setup
    engine.provider=JevProvider(api_key="")
    run=round_now(engine,driver)
    assert run["status"] == "waiting_credentials" and driver.calls == 0
    assert engine.store.rows("jobs")[0]["status"] == "waiting_credentials"
    assert engine.store.get("state","active_documents") is None


def test_fencing_rejects_old_executor_and_allows_recovery(setup):
    engine,web,driver=setup
    clock=[1000.]
    engine.store.clock=lambda:clock[0]
    first=engine.store.claim("first",10,600)
    engine.poll(first,True)
    engine.select_jobs(first)
    assert engine.store.claim("second",10,600) is None
    clock[0]+=121
    second=engine.store.claim("second",10,600)
    with pytest.raises(KBError,match="STALE_EXECUTOR"): engine.classify(first["id"],first["jobs"][0])
    engine.select_jobs(second)
    driver.run(engine,second)
    engine.publish_documents(second)
    engine.store.finish(second,"completed")
    with pytest.raises(KBError,match="STALE_EXECUTOR"): engine.store.finish(first,"completed")


def test_candidate_exact_quotes_and_ids_are_required(setup):
    engine,web,driver=setup
    run=engine.store.claim("test",120,600)
    engine.poll(run,True)
    key=engine.select_jobs(run)[0]
    engine.classify(run["id"],key)
    bad={"kind":"Material","name":"虚构证明","quote":"虚构证明必须提交。","source_segment_ids":["invented"]}
    with pytest.raises(KBError,match="UNSUPPORTED_CLAIM"): engine.submit(run["id"],key,[bad])


def test_human_review_base_change_requires_revalidation(setup):
    engine,web,driver=setup
    web["body"]='<article id="policy"><p>模拟甲事项需申请表。</p><p>'+('乙'*3500)+'</p></article>'
    round_now(engine,driver)
    candidates=engine.store.rows("candidates")
    assert len(candidates)>=2
    first=engine.review_candidate(candidates[0]["id"],None,"human",acknowledge=True)
    with pytest.raises(KBError,match="REVALIDATION_REQUIRED"):
        engine.review_candidate(candidates[1]["id"],first,"human",acknowledge=True)
    engine.requeue_candidate(candidates[1]["id"])
    round_now(engine,driver)
    assert engine.store.get("candidates",candidates[1]["id"])["validation"]["base_rules"]==first


def test_updates_keep_historical_originals_and_rollback(setup):
    engine,web,driver=setup
    round_now(engine,driver)
    old=engine.store.get("state","active_documents")
    old_data=deepcopy(engine.store.version(old))
    web["body"]='<article id="policy">模拟事项需提交申请表和演练证明。</article>'
    round_now(engine,driver)
    new=engine.store.get("state","active_documents")
    assert old!=new and engine.store.version(old)==old_data
    assert "演练证明" in engine.search("演练证明")["items"][0]["content"]
    engine.store.rollback("documents",old,new,"human")
    assert engine.search("演练证明")["status"] == "EMPTY"
    with pytest.raises(KBError,match="BASE_VERSION_CHANGED"): engine.store.rollback("documents",new,new,"human")


def test_large_source_queues_across_rounds_and_publishes_only_complete_source(setup):
    engine,web,driver=setup
    web["body"]='<article id="policy">'+''.join('<p>第'+str(i)+'段申请表。</p>' for i in range(25))+'</article>'
    round_now(engine,driver)
    assert len(engine.store.rows("jobs"))==5
    assert engine.store.get("state","active_documents") is None
    round_now(engine,driver)
    version=engine.store.version(engine.store.get("state","active_documents"))
    assert sum(len(a["segments"]) for a in version["data"]["assets"]) == 25


def test_malformed_embedding_does_not_switch_active_version(setup):
    engine,web,driver=setup
    round_now(engine,driver)
    old=engine.store.get("state","active_documents")
    web["body"]='<article id="policy">新的申请表内容。</article>'
    engine.embedder=lambda texts:[[float("nan")]]
    round_now(engine,driver)
    assert engine.store.get("state","active_documents")==old


def test_public_url_cannot_target_local_or_unregistered_hosts(setup):
    engine,web,driver=setup
    source=deepcopy(engine.config["sources"][0])
    source.update(data_class="public",url="https://www.gov.cn/policy",allowed_hosts=["www.gov.cn"])
    fetcher=Fetcher(engine.store,resolver=lambda *a,**kw:[(0,0,0,0,("127.0.0.1",443))])
    with pytest.raises(KBError,match="NON_PUBLIC_ADDRESS"): fetcher.allowed(source,source["url"])
    with pytest.raises(KBError,match="URL_NOT_REGISTERED"): fetcher.allowed(source,"http://169.254.169.254/")


def test_unsupported_attachment_is_not_a_successful_check(setup):
    engine,web,driver=setup
    engine.config["sources"][0]["attachment_urls"]=[{"url":"http://fixture.test/a.pdf","format":"pdf"}]
    round_now(engine,driver)
    assert engine.store.rows("sources")[0]["error"]=="UNSUPPORTED_FORMAT"
    assert not engine.store.rows("jobs")


def test_original_objects_and_version_content_are_integrity_checked(setup):
    engine,web,driver=setup
    round_now(engine,driver)
    version=engine.store.version(engine.store.get("state","active_documents"))
    sha=version["data"]["assets"][0]["sha256"]
    (engine.store.root/"objects"/sha).write_bytes(b"changed")
    with pytest.raises(KBError,match="OBJECT_INTEGRITY"): engine.store.read_object(sha)
    with pytest.raises(KBError,match="INVALID_OBJECT"): engine.store.read_object("../private")


def test_workbench_requires_maintainer_and_same_origin(setup):
    engine,web,driver=setup
    roles={"tenant":{"human":"maintainer","service":"service"}}
    def auth(body): return {"tenant_id":"tenant","user_id":body["email"]} if body["password"]=="test" else None
    app=create_app(engine,driver,"test-dedicated-token-value-32-characters",roles,authenticator=auth,start_worker=False)
    with TestClient(app,base_url="http://127.0.0.1:8775") as client:
        assert client.get('/api/state').status_code==401
        assert client.post('/api/login',json={"email":"human","password":"test"}).status_code==403
        headers={"Origin":"http://127.0.0.1:8775"}
        assert client.post('/api/login',headers=headers,json={"email":"service","password":"test"}).status_code==401
        assert client.post('/api/login',headers=headers,json={"email":"human","password":"test"}).status_code==200
        assert client.get('/api/state').status_code==200
        assert client.post('/api/actions/check',headers=headers,json={"tenant_id":"spoofed"}).status_code==409
        assert client.post('/api/actions/pause',headers=headers,json={}).status_code==200
        assert engine.store.get('state','paused') is True
        assert client.post('/api/actions/review',headers=headers,json={"candidate_id":"unknown","accept":True,"acknowledge":False}).status_code==409
        assert client.post('/api/logout',headers=headers,json={}).status_code==200
        assert client.get('/api/state').status_code==401


def test_synthetic_fixture_is_inert_but_raw_html_and_parser_flow_remain(setup):
    engine, _, driver = setup
    roles = {"tenant": {"human": "maintainer"}}
    def auth(body): return {"tenant_id": "tenant", "user_id": "human"}
    app = create_app(engine, driver, "test-token", roles, authenticator=auth, start_worker=False)
    html = '<article id="policy">模拟申请表。<script>window.fixtureExecuted=true</script></article><form action="/api/login"></form>'
    with TestClient(app, base_url="http://127.0.0.1:8775") as client:
        headers = {"Origin": "http://127.0.0.1:8775"}
        assert client.post("/api/actions/fixture", headers=headers, json={"html": html}).status_code == 401
        assert client.post("/api/login", headers=headers, json={"email": "human", "password": "fixture"}).status_code == 200
        assert client.post("/api/actions/fixture", headers=headers, json={"html": html}).status_code == 200
        client.cookies.clear()
        response = client.get("/fixture/policy")
        assert response.status_code == 200 and response.text == html
        policy = {part.strip() for part in response.headers["content-security-policy"].split(";")}
        assert policy == {"sandbox", "default-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"}
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        parser = BodyParser({"tag": "article", "id": "policy"})
        parser.feed(response.text)
        assert parser.text() == "模拟申请表。"
        engine.synthetic = False
        assert client.get("/fixture/policy").status_code == 404


def test_empty_source_registry_is_idle_and_never_calls_model(setup):
    engine,web,driver=setup
    engine.config['sources']=[]
    worker=Worker(engine,driver)
    worker.tick()
    assert engine.store.get('state','heartbeat')['status']=='waiting_sources'
    assert driver.calls==0 and not engine.store.rows('runs')


def test_completed_run_cannot_be_overwritten_by_shutdown(setup):
    engine,web,driver=setup
    run=round_now(engine,driver)
    engine.store.abort(run,'interrupted')
    assert engine.store.get('runs',run['id'])['status']=='completed'


def test_restart_after_processing_resumes_publication_without_rerunning_models(setup):
    engine,web,driver=setup
    run=engine.store.claim('old',120,600)
    engine.poll(run,True)
    engine.select_jobs(run)
    driver.run(engine,run)
    engine.store.abort(run,'interrupted')
    assert engine.needs_publication()
    worker=Worker(engine,driver)
    worker.tick()
    worker.thread.join(3)
    assert engine.store.get('state','active_documents')
    assert driver.calls==1


def test_known_changed_source_hides_outdated_formal_claims_until_review(setup):
    engine,web,driver=setup
    round_now(engine,driver)
    c=engine.store.rows('candidates')[0]
    formal=engine.review_candidate(c['id'],None,'human',acknowledge=True)
    web['body']='<article id="policy">模拟事项需提交申请表和其他证明。</article>'
    round_now(engine,driver)
    assert engine.search('申请表','rules')['status']=='EMPTY'
    assert engine.search('申请表','rules',formal)['items']
    assert engine.search('申请表')['items']


def test_detected_change_with_missing_key_also_withholds_old_formal_rules(setup):
    from gov_mcp.classification import JevProvider
    engine,web,driver=setup
    round_now(engine,driver)
    c=engine.store.rows('candidates')[0]
    engine.review_candidate(c['id'],None,'human',acknowledge=True)
    old=engine.store.get('state','active_documents')
    web['body']='<article id="policy">模拟事项的材料要求已经调整。</article>'
    engine.provider=JevProvider(api_key='')
    round_now(engine,driver)
    assert engine.store.get('state','active_documents')==old
    result=engine.search('申请表','rules')
    assert result['status']=='EMPTY' and '等待复核' in result['notice']


def test_approved_ontology_change_is_versioned_and_reused(setup):
    engine,web,driver=setup
    run=engine.store.claim('owner',120,600)
    engine.poll(run,True)
    key=engine.select_jobs(run)[0]
    engine.classify(run['id'],key)
    candidate=engine.submit(run['id'],key,[],[{'type':'entity_type','name':'ServiceChannel','description':'经人工确认的服务渠道类型'}])
    engine.validate(run['id'],candidate['id'],{'omissions':[],'conflicts':[],'notes':[]})
    engine.finish_job(run['id'],key,'等待人工复核本体建议。')
    engine.publish_documents(run)
    engine.store.finish(run,'completed')
    before=engine.processing_hash
    assert 'ServiceChannel' not in engine.ontology()['entity_types']
    version=engine.review_candidate(candidate['id'],None,'human',acknowledge=True)
    assert 'ServiceChannel' in engine.ontology()['entity_types']
    assert engine.processing_hash!=before
    assert engine.store.version(version)['data']['ontology_version']


@pytest.mark.parametrize('field,value',[('heartbeat_seconds',0),('max_segments',2),('max_documents',4),('run_budget_seconds',False)])
def test_invalid_runtime_limits_rejected_before_startup(setup,field,value):
    engine,web,driver=setup
    from gov_mcp.knowledge.engine import validate_settings
    config=deepcopy(engine.config)
    config[field]=value
    with pytest.raises(KBError,match='INVALID_CONFIG'): validate_settings(config)


def test_unapproved_sources_do_not_make_network_requests(setup):
    engine,web,driver=setup
    engine.config['sources'][0]['approved']=False
    round_now(engine,driver)
    assert web['calls']==0 and driver.calls==0
    assert engine.store.rows('sources')[0]['error']=='SOURCE_NOT_APPROVED'


def test_ocr_polygons_remain_numeric_and_source_bound(setup):
    engine,web,driver=setup
    from gov_mcp.knowledge.store import encoded
    class Scalar:
        def __float__(self): return 1.5
    engine.fetcher.ocr=lambda raw:[([[Scalar(),Scalar()]]*4,'申请表',.99)]
    source=engine.config['sources'][0]
    body,assets,raw=engine.fetcher.parse(source,{'url':source['url'],'format':'png'},b'synthetic-image-bytes','image/png')
    polygon=assets[0]['segments'][0]['locator']['polygon']
    assert all(type(v) is float for pair in polygon for v in pair)
    assert encoded(assets) and assets[0]['sha256']==raw

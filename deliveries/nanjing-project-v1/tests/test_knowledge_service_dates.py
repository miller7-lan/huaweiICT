"""Synthetic service-information dates; no official approval or network access."""

import copy
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore, encode, fingerprint
from gov_mcp.knowledge_answers import select_citations
from gov_mcp.knowledge_fetch import observe_html, parse_html, worker_once
from gov_mcp.knowledge_tools import KnowledgeTools


@pytest.fixture
def service(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "service-dates")
    observed = (date.today() - timedelta(days=10)).isoformat()
    raw = ('<html><head><meta name="PubDate" content="'+observed+' 09:18:04.0"></head>'
           '<body id="article"><p>纯模拟服务信息：身份证到期换领模拟时限15个工作日。</p></body></html>').encode()
    config = {"id":"fixture-service", "label":"纯模拟服务信息", "enabled":True,
        "origin":"https://fixtures.invalid", "allowed_paths":["/guide"], "seeds":["https://fixtures.invalid/guide"],
        "interval_seconds":3600, "delay_seconds":5, "content_selector":{"id":"article"},
        "metadata":{"title":"纯模拟办事说明", "region":"郑州市二七区", "matter":"demo-id-renewal",
            "document_type":"official_service_information", "scope_confirmed":True,
            "effective_from":None, "effective_to":None, "published_at":None, "usage_basis":"Self-authored synthetic fixture"},
        "access_review":{"approved":True,"reviewer":"fixture","actor_kind":"synthetic","usage_basis":"No network",
            "checked_at":datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    meta = dict(config["metadata"], source_observation=observe_html(raw))
    chunks,_ = parse_html(raw,config["content_selector"])
    base = store.ingest(config["id"],config["seeds"][0],raw,meta,chunks)
    with store.db() as db:
        row,_ = store._verify(db,base,require_review=False)
        db.execute("UPDATE sites SET last_success=?",(time.time(),))
    basis = {"kind":"reported_service_information", "information_as_of":observed, "base_revision":base,
        "original_sha256":row["sha256"], "body_sha256":row["body_sha256"],
        "source_observation_sha256":fingerprint(meta["source_observation"]),
        "source_date_field":"meta:pubdate", "source_date_value":observed+" 09:18:04.0",
        "legal_commencement_established":False}
    meta = dict(meta,published_at=observed,date_basis=basis)
    return store,config,raw,chunks,base,meta


@pytest.mark.parametrize("literal",["2020-01-01 00:002021-11-16 15:10", "2020-01-01 09:00\n2020-01-01 09:00", "2999-01-01 09:00"])
def test_ambiguous_or_future_publication_value_cannot_become_a_service_date(service,literal):
    store,config,raw,chunks,_,metadata=service
    changed=raw.replace(metadata["date_basis"]["source_date_value"].encode(),literal.encode())
    new_meta=dict(config["metadata"],source_observation=observe_html(changed))
    base=store.ingest(config["id"],config["seeds"][0]+"?date-fixture",changed,new_meta,chunks)
    with store.db() as db:row,_=store._verify(db,base,require_review=False)
    info=literal[:10];new_meta.update(published_at=info,date_basis=dict(metadata["date_basis"],base_revision=base,
        information_as_of=info,original_sha256=row["sha256"],body_sha256=row["body_sha256"],
        source_observation_sha256=fingerprint(new_meta["source_observation"]),source_date_value=literal))
    revision=store.ingest(config["id"],config["seeds"][0]+"?date-fixture",changed,new_meta,chunks)
    with pytest.raises(ValueError):store.review(revision,"synthetic-date-reviewer",confirmed=True)


def test_actual_worker_preserves_service_review_only_for_same_bound_source(service):
    store,config,raw,_,_,_=service
    revision=reviewed(service)
    class Body:
        def __init__(self,body):self.body=body
        def fetch(self,config,url):return 200,{"content-type":"text/html"},self.body,url
    for body,same in [(raw,True),(raw.replace('15个工作日'.encode(),'16个工作日'.encode()),False)]:
        with store.db() as db:
            db.execute("INSERT INTO jobs(site_id,url,due) VALUES(?,?,0) ON CONFLICT(site_id,url) DO UPDATE SET state='queued',attempts=0,lease_until=NULL,error=NULL",(config["id"],config["seeds"][0]))
        assert worker_once(store,fetcher=Body(body),owner="synthetic-service-date-worker")
        with store.db() as db:
            latest=db.execute("SELECT * FROM revisions ORDER BY rowid DESC LIMIT 1").fetchone()
            if same:
                assert latest["id"]==revision
                store._verify(db,revision)
            else:
                assert latest["id"]!=revision and latest["state"]=="pending_review"
                with pytest.raises(ValueError):store.review(latest["id"],"synthetic-date-reviewer",confirmed=True)


def reviewed(service, meta=None):
    store,config,raw,chunks,_,original_meta = service
    revision = store.ingest(config["id"],config["seeds"][0],raw,meta or original_meta,chunks)
    store.review(revision,"synthetic-date-reviewer",confirmed=True)
    return revision


def test_bound_service_information_review_keeps_legal_commencement_unknown(service):
    store,*_ = service
    revision = reviewed(service)
    with store.db() as db:
        row,_ = store._verify(db,revision)
    assert '"effective_from":null' in row["metadata"]
    assert store.status()["counts"]["heads"] == 0


@pytest.mark.parametrize("field,value",[
    ("kind","commencement"), ("information_as_of","1999-01-01"),
    ("original_sha256","0"*64), ("body_sha256","0"*64),
    ("source_observation_sha256","0"*64), ("source_date_field","meta:lastmodifiedtime"),
    ("source_date_value","1999-01-01 09:18:04.0"), ("legal_commencement_established",True),
    ("unexpected","unbound extension")])
def test_unbound_or_ambiguous_service_date_is_rejected(service,field,value):
    meta=copy.deepcopy(service[-1]);meta["date_basis"][field]=value
    with pytest.raises(ValueError):reviewed(service,meta)


@pytest.mark.parametrize("doctype",["statute","administrative_norm","provincial_normative_document","synthetic"])
def test_service_date_cannot_replace_normative_commencement(service,doctype):
    meta=copy.deepcopy(service[-1]);meta["document_type"]=doctype
    with pytest.raises(ValueError):reviewed(service,meta)


@pytest.mark.parametrize("field,value",[("effective_from","2020-01-01"),("effective_to","2030-01-01"),("published_at",None),("scope_confirmed",False),("conflict",True)])
def test_service_date_does_not_bypass_other_review_gates(service,field,value):
    meta=copy.deepcopy(service[-1]);meta[field]=value
    with pytest.raises(ValueError):reviewed(service,meta)


def test_service_date_cannot_bind_another_document_even_with_same_bytes(service):
    store,config,raw,chunks,_,metadata=service
    other=store.ingest(config["id"],config["seeds"][0]+"?other",raw,
        dict(metadata,date_basis=None,published_at=None),chunks)
    meta=copy.deepcopy(metadata);meta["date_basis"]["base_revision"]=other
    with pytest.raises(ValueError):reviewed(service,meta)


def test_service_information_is_date_filtered_and_distinct_in_bound_citations(service):
    store,_,_,_,_,meta=service
    revision=reviewed(service)
    version=store.candidate("郑州市二七区","demo-id-renewal")
    # A synthetic published-state fixture exercises reading only, not business acceptance.
    with store.db() as db:
        db.execute("UPDATE versions SET state='published' WHERE id=?",(version,))
        db.execute("INSERT INTO heads VALUES(?,?)",(encode(["郑州市二七区","demo-id-renewal"]),version))
    (store.root/"knowledge-scope.json").write_text(encode({"region":"郑州市二七区","matter":"demo-id-renewal"}),encoding="utf-8")
    (store.root/"knowledge-role-map.json").write_text(encode({"tenant":{r:{"role":r} for r in ["citizen","staff"]}}),encoding="utf-8")
    observed=meta["date_basis"]["information_as_of"]
    before=(date.fromisoformat(observed)-timedelta(days=1)).isoformat()
    assert store.search("身份证到期换领","郑州市二七区","demo-id-renewal",as_of=before)["citations"] == []
    for role in ["citizen","staff"]:
        value=KnowledgeTools(store.root).execute("search_evidence","tenant",role,query="身份证到期换领",as_of=observed)
        citation,=value["citations"]
        assert citation["revision_id"]==revision and citation["effective_from"] is None
        assert citation["information_as_of"]==observed and citation["date_basis"]=="reported_service_information"
        assert "服务信息截至日期："+observed in value["data"]["answer"]
        assert "不能据此推定施行日" in value["data"]["answer"]
        changed=copy.deepcopy(value["citations"]);changed[0]["information_as_of"]=before
        with pytest.raises(ValueError):select_citations(changed,version)

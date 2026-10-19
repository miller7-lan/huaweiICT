"""Regression contracts for bounded admission, repeated updates and cached execution."""
import asyncio
from copy import deepcopy

import pytest
from starlette.testclient import TestClient

from gov_mcp.decision import service as decision_module
from gov_mcp.decision.models import KnowledgeSnapshot, canonical_hash
from gov_mcp.decision.server import create_app
from gov_mcp.decision.workflow import Workflow, WorkflowError, vector

FACTS = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}
BRANCH = "branch-adult-self"
ACTOR = "demo-professional"


@pytest.fixture
def w(tmp_path):
    return Workflow(tmp_path)


def updated(w, profile="changed", proposals=False):
    w.configure_demo(actor=ACTOR)
    w.set_profile(profile, actor=ACTOR)
    w.heartbeat(force=True)
    p = w.rows("package")[0]
    keys = [c["object_id"] for c in p["candidates"]]
    if proposals: keys += [v["id"] for v in p["proposals"]]
    w.review(p["id"], keys, actor=ACTOR, decision="accepted", note="模拟复核")
    return w.publish(p["id"], keys, w.version()["id"], actor=ACTOR)


@pytest.mark.parametrize("path,chunk_size,limit", [("/login", 32768, 65536), ("/mcp/", 262144, 1048576)])
@pytest.mark.parametrize("length", [None, "1"])
def test_stream_budget_stops_reading_before_the_remaining_body(w, path, chunk_size, limit, length):
    app = create_app(w, "professional", "service", start_worker=False)
    reads, sent = [], []
    async def receive():
        reads.append(1)
        assert len(reads) < 20, "middleware consumed an unbounded body"
        return {"type": "http.request", "body": b"x"*chunk_size, "more_body": True}
    async def send(message): sent.append(message)
    headers = [(b"host", b"127.0.0.1:8780"), (b"origin", b"http://127.0.0.1:8780"), (b"authorization", b"Bearer service")]
    if length: headers.append((b"content-length", length.encode()))
    scope = {"type": "http", "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "root_path": "", "query_string": b"", "headers": headers, "server": ("127.0.0.1", 8780), "client": ("127.0.0.1", 1111), "http_version": "1.1"}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert len(reads) == limit//chunk_size+1
    assert not w.rows("query")


def client(w):
    return TestClient(create_app(w, "professional", "service", start_worker=False), base_url="http://127.0.0.1:8780",
                      headers={"Origin": "http://127.0.0.1:8780"})


def test_anonymous_churn_cannot_block_professional_login(w):
    with client(w) as c:
        for _ in range(1030):
            c.cookies.clear()
            assert c.post("/login", json={"role": "citizen"}).status_code == 200
        assert c.post("/login", json={"role": "professional", "token": "wrong"}).status_code == 403
        assert c.get("/api/status").status_code == 403
        assert c.post("/login", json={"role": "professional", "token": "professional"}).status_code == 200
        assert c.get("/api/status").status_code == 200


def test_relogin_preserves_owned_history_and_request_retry_checks_live_gate(w):
    with client(w) as c:
        c.post("/login", json={"role": "citizen"})
        cookie = c.cookies.get("gov_workflow_session")
        payload = {"facts": FACTS, "branch_id": BRANCH, "request_id": "same-browser-request"}
        a = c.post("/api/query", json=payload).json()
        c.post("/login", json={"role": "citizen"})
        assert c.cookies.get("gov_workflow_session") == cookie
        assert c.get("/api/history", params={"task_id": a["task_id"]}).status_code == 200
        w.configure_demo(actor=ACTOR)
        w.set_profile("changed", actor=ACTOR)
        w.heartbeat(force=True)
        replay = c.post("/api/query", json=payload).json()
        assert replay["task_id"] == a["task_id"] and replay["idempotent_replay"]
        assert replay["paused"] and len(w.rows("query")) == 1
        assert c.post("/api/query", json={**payload, "facts": {}}).status_code == 409


def test_failed_retrieval_is_durable_and_sanitized(w):
    def fail(*args): raise TimeoutError("sensitive provider payload")
    with pytest.raises(TimeoutError): w.query(FACTS, BRANCH, retriever=fail)
    task = w.rows("query")[0]
    assert task["status"] == "failed" and task["error_code"] == "TimeoutError"
    assert "sensitive" not in str(task)
    assert w.history(task["id"])["status"] == "failed_retry_as_new_task"


def test_repeated_publications_keep_active_graph_bounded_and_old_sources(w):
    initial = w.version()
    updated(w, proposals=True)
    for i in range(18): updated(w, "baseline" if i % 2 == 0 else "changed", proposals=True)
    current = w.version()["snapshot"]
    KnowledgeSnapshot.model_validate_json(__import__("json").dumps(current))
    assert len(current["assets"]) < 10
    assert len(current["aliases"]) == len({a["id"] for a in current["aliases"]})
    assert len(current["reviews"]) < 50
    assert w.version(initial["id"]) == initial
    assert w.documents("initial")["assets"] == initial["snapshot"]["assets"]


def test_ontology_registry_tracks_selected_version_on_rollback_and_restart(w):
    initial = w.version()["id"]
    updated(w, proposals=True)
    new = w.version()["id"]
    assert w.status()["ontology"]
    w.rollback(initial, new, actor=ACTOR)
    assert not w.status()["ontology"]
    restarted = Workflow(w.store.root)
    assert not restarted.status()["ontology"]
    restarted.rollback(new, initial, actor=ACTOR)
    assert restarted.status()["ontology"]


def test_cached_service_never_shares_gate_tasks_or_returned_values(w):
    with w.store.db() as db:
        v = w.version(db=db)
        a, b = w._service(v, db), w._service(v, db)
    assert a._snapshot is b._snapshot
    answer = a.answer({"reason": "expiry"}, BRANCH)
    answer["follow_up"]["fields"][0]["label"] = "篡改"
    a.availability.suspend("req-slip", change_id="simulated", reason="模拟")
    assert not b._tasks and not b.availability.suspended
    again = b.answer({"reason": "expiry"}, BRANCH)
    assert all(f["label"] != "篡改" for f in again["follow_up"]["fields"])
    damaged = deepcopy(v)
    damaged["snapshot"]["assets"][0]["text"] += "篡改"
    with w.store.db() as db:
        with pytest.raises(WorkflowError, match="VERSION_INTEGRITY"): w._service(damaged, db)


def test_each_condition_is_calculated_once_per_preparation(w, monkeypatch):
    with w.store.db() as db: s = w._service(w.version(db=db), db)
    original, calls = decision_module.evaluate, []
    def counted(expression, facts, fields):
        calls.append(canonical_hash(expression))
        return original(expression, facts, fields)
    monkeypatch.setattr(decision_module, "evaluate", counted)
    s.prepare(FACTS, BRANCH)
    assert len(calls) == len(set(calls))
    first_count = len(calls)
    s.prepare({**FACTS, "electronic_verified": True}, BRANCH)
    assert len(calls) == first_count*2  # No fact-dependent cache leaks between requests.


def test_record_pages_do_not_hide_older_tasks_or_load_all_payloads(w):
    # Minimal persisted query metadata is sufficient for listing, not for answering.
    with w.store.db(True) as db:
        for number in range(55):
            key = "task-"+str(number)
            w.put("query", key, {"id": key, "created_at": number, "status": "failed", "version_id": "fixture", "parent_task": None}, db)
    first, second = w.status(), w.status(1)
    assert len(first["queries"]) == 50 and len(second["queries"]) == 5
    assert first["has_more"] and not second["has_more"]
    assert first["record_counts"]["query"] == 55
    assert {q["id"] for q in first["queries"]}.isdisjoint(q["id"] for q in second["queries"])
    for page in (-1, True, 1.1, 10001):
        with pytest.raises(WorkflowError, match="INVALID_PAGE"): w.status(page)


@pytest.mark.parametrize("text", ["申请旧卡", "保持条码清晰", "电子表", "不存在的术语", ""])
def test_inverted_search_matches_exhaustive_cosine_and_checks_sources(w, text):
    updated(w)
    docs = w.documents()
    q = vector(text)
    expected = []
    for e in docs["evidence"]:
        v = vector(e["quote"])
        score = sum(weight*v.get(k, 0) for k, weight in q.items())
        if score > 0: expected.append({"id": e["id"], "score": round(score, 5)})
    expected.sort(key=lambda r: (-r["score"], r["id"]))
    hits = w.search_documents(text)
    assert [{k: h[k] for k in ("id", "score")} for h in hits["hits"]] == expected[:20]
    assert hits["search_stats"]["scored_spans"] <= len(docs["evidence"])
    # A warm index never hides tampering of its backing immutable source.
    docs["assets"][0]["text"] += "篡改"
    w.store.put("state", "wf_documents", docs)
    with pytest.raises(WorkflowError, match="DOCUMENTS_INTEGRITY"): w.search_documents(text)

import json
import threading
from types import SimpleNamespace

from m1_ui_trace_proxy import M1Handler


def handler(path, method="POST"):
    value = object.__new__(M1Handler)
    value.path, value.command = path, method
    value.headers = {}
    value.server = SimpleNamespace(agent_ids={4,5,6}, sidecar_conversations=set(), observation_lock=threading.Lock())
    return value


def test_only_registered_m1_agents_use_sidecar():
    h = handler("/api/agent/run")
    assert h.upstream(json.dumps({"agent_id":4}).encode()) == "http://127.0.0.1:5016/agent/run"
    assert h.upstream(json.dumps({"agent_id":1}).encode()) == "http://127.0.0.1:3000/api/agent/run"
    assert h.upstream(json.dumps({"agent_id":"4"}).encode()) == "http://127.0.0.1:3000/api/agent/run"
    assert h.upstream(b"invalid") == "http://127.0.0.1:3000/api/agent/run"


def test_login_and_other_apis_are_never_rerouted():
    for path in ("/api/user/signin", "/api/agent/import", "/api/conversation/create", "/zh/newchat"):
        h = handler(path)
        assert h.upstream(b'{"agent_id":4}') == "http://127.0.0.1:3000" + path


def test_stop_routes_only_observed_sidecar_conversations():
    h = handler("/api/agent/run")
    h.upstream(b'{"agent_id":4}')
    h.observe_conversation(101, True)
    h.path,h.command="/api/agent/stop/101","GET"
    assert h.upstream(b"") == "http://127.0.0.1:5016/agent/stop/101"
    h.path="/api/agent/stop/102"
    assert h.upstream(b"") == "http://127.0.0.1:3000/api/agent/stop/102"


def test_sidecar_auth_uses_request_cookie_and_preserves_existing_header():
    h = handler("/api/agent/run")
    h.headers = {"Cookie": "nexent_access_token=technical-session-token"}
    h.upstream(b'{"agent_id":4}')
    assert h.forwarded_headers({}) == {"Authorization":"Bearer technical-session-token"}
    assert h.forwarded_headers({"authorization":"Bearer provided"}) == {"authorization":"Bearer provided"}
    h.upstream(b'{"agent_id":1}')
    assert h.forwarded_headers({}) == {}

"""Small client for the pinned native Nexent configuration and runtime APIs."""

import json
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


class NexentAPI:
    def __init__(self, base="http://127.0.0.1:5010", runtime_base="http://127.0.0.1:5014"):
        self.base, self.runtime_base = base.rstrip("/"), runtime_base.rstrip("/")
        self.http = httpx.Client(trust_env=False, timeout=60)
        self.token = None

    def call(self, method, path, body=None):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = self.http.request(method, self.base + path, headers=headers, json=body)
        if response.status_code >= 400:
            # Response bodies may contain credentials. Retain only status/path.
            raise RuntimeError(f"Nexent {method} {path}: HTTP {response.status_code}")
        return response.json()

    def login(self, credentials):
        data = self.call("POST", "/user/signin", credentials)["data"]
        self.token = data["session"]["access_token"]
        return self.call("GET", "/user/current_user_info")["data"]["user"]

    def close(self):
        self.http.close()


def save_state(state):
    pending = RUNTIME / "nexent-state.json.part"
    pending.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(RUNTIME / "nexent-state.json")


def activate_model(api, state, model):
    model_id = state["models"][model]
    versions = {}
    # Publish children first; native publishing pins their current versions.
    for name in ("citizen_assistant", "staff_assistant", "gov_coordinator"):
        agent_id = state["agents"][name]
        api.call("POST", "/agent/update", {
            "agent_id": agent_id, "model_ids": [model_id],
            "model_params_override": {str(model_id): {"temperature": .1}},
            "requested_output_tokens": 1024, "allow_chat_metadata": False,
            "enable_protocol_repair_retry": False,
        })
        published = api.call("POST", f"/agent/{agent_id}/publish", {
            "version_name": f"blank-{model}", "release_note": "Empty technical verification; no business assets"})
        versions[name] = published["version_no"]
    state["active_model"] = model
    state["versions"] = versions
    save_state(state)
    return versions

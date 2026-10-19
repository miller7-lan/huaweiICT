"""Nexent transport adapter: role model settings live outside knowledge logic."""

import json
import time

import httpx

from .store import KBError


class NativeDriver:
    def __init__(self, root, *, config_url="http://host.docker.internal:5010", runtime_url="http://host.docker.internal:5014"):
        self.root, self.config_url, self.runtime_url = root, config_url, runtime_url

    def _session(self):
        config = json.loads((self.root / "native.json").read_text(encoding="utf-8"))
        credentials = json.loads((self.root / "service-account.json").read_text(encoding="utf-8"))
        client = httpx.Client(trust_env=False, timeout=30)
        try:
            response = client.post(self.config_url+"/user/signin", json=credentials)
            response.raise_for_status()
            token = response.json()["data"]["session"]["access_token"]
            client.headers["Authorization"] = "Bearer "+token
            return client, config
        except Exception:
            client.close()
            raise KBError("NEXENT_AUTH_UNAVAILABLE") from None

    def run(self, engine, run):
        if not (self.root / "native.json").exists(): raise KBError("WAITING_NATIVE_CONFIGURATION")
        client, native = self._session()
        events = []
        conversation = None
        try:
            response = client.put(self.runtime_url+"/conversation/create", json={"title": "知识维护-"+run["id"][:8]})
            response.raise_for_status()
            conversation = response.json()["data"]["conversation_id"]
            run["conversation_id"] = conversation
            with engine.store.db(True) as db:
                engine.store.assert_lease(run, db)
                engine.store.put("runs", run["id"], run, db)
            payload = {"query": "Process only knowledge maintenance run_id="+run["id"],
                "conversation_id": conversation, "agent_id": native["controller_id"],
                "model_id": native["controller_model_id"], "version_no": native["versions"]["controller"],
                "is_debug": False, "history": [], "requested_output_tokens": 1024,
                "enable_plan": False, "enable_automation_tool": False}
            with client.stream("POST", self.runtime_url+"/agent/run", json=payload, timeout=httpx.Timeout(90, connect=10)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    with engine.store.db() as db: engine.store.assert_lease(run, db)
                    if line.startswith("data:") and line[5:].strip():
                        event = json.loads(line[5:].strip())
                        events.append(event)
            # Traces are private and tied to run; public verification exports only summary.
            traces = self.root / "traces"
            traces.mkdir(exist_ok=True)
            (traces / (run["id"]+".json")).write_text(json.dumps({"run_id": run["id"], "conversation_id": conversation,
                "agent_configuration": native, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
            if any(e.get("type") in {"error", "max_steps_reached"} for e in events): raise KBError("NATIVE_EXECUTION_FAILED")
            if not any(e.get("type") == "final_answer" and not e.get("depth", 0) for e in events): raise KBError("NATIVE_FINAL_MISSING")
            return events
        except Exception:
            if run.get("conversation_id"):
                try: client.get(self.runtime_url+"/agent/stop/"+str(run["conversation_id"]), timeout=10).raise_for_status()
                except httpx.HTTPError: raise KBError("NATIVE_STOP_UNCONFIRMED") from None
            raise
        finally:
            traces = self.root / "traces"
            traces.mkdir(exist_ok=True)
            (traces / (run["id"]+".json")).write_text(json.dumps({"run_id": run["id"], "conversation_id": conversation,
                "agent_configuration": native, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
            client.close()

    def stop(self, run):
        if not run.get("conversation_id"): return
        client, _ = self._session()
        try:
            client.get(self.runtime_url+"/agent/stop/"+str(run["conversation_id"]), timeout=10).raise_for_status()
        finally:
            client.close()


def release_local(config, role):
    selected = config["models"][role]
    if selected["provider"] != "nexent" or not selected.get("local",selected["model"].startswith("gov-qwen3-")): return
    with httpx.Client(trust_env=False, timeout=15) as client:
        client.post(config.get("local_model_url", "http://host.docker.internal:11434").rstrip("/")+"/api/generate",
            json={"model": selected["model"], "keep_alive": 0}).raise_for_status()

"""Dedicated M1 native UI entry; route only registered govx agent runs to the sidecar."""

import argparse
import json
import re
import threading
from http.server import ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path

from nexent_api import RUNTIME
from ui_trace_proxy import Handler


class M1Handler(Handler):
    def upstream(self, body):
        self.sidecar_run = False
        path = self.path.split("?", 1)[0]
        if self.command == "POST" and path == "/api/agent/run":
            try:
                payload = json.loads(body)
                agent_id = payload.get("agent_id") if isinstance(payload, dict) else None
                if type(agent_id) is int and agent_id in self.server.agent_ids:
                    self.sidecar_run = True
                    return "http://127.0.0.1:5016" + self.path.removeprefix("/api")
            except (ValueError, TypeError):
                pass
        stop = re.fullmatch(r"/api/agent/stop/(\d+)", path)
        if stop:
            with self.server.observation_lock:
                owned = int(stop.group(1)) in self.server.sidecar_conversations
            if owned:
                self.sidecar_run = True
                return "http://127.0.0.1:5016" + self.path.removeprefix("/api")
        return super().upstream(body)

    def forwarded_headers(self, headers):
        # Pinned frontend/server.js performs this exact Cookie -> Bearer
        # adaptation for runtime HTTP/SSE. The runtime still validates the JWT;
        # no technical account credentials or guessed identity are injected.
        if self.sidecar_run and not any(k.lower() == "authorization" for k in headers):
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            token = cookies.get("nexent_access_token")
            if token is not None and token.value:
                headers = {**headers, "Authorization": "Bearer " + token.value}
        return headers

    def observe_conversation(self, conversation_id, capture):
        if self.sidecar_run and capture and type(conversation_id) is int:
            with self.server.observation_lock:
                self.server.sidecar_conversations.add(conversation_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--output", type=Path, default=RUNTIME / "m1/sidecar-ui-traces")
    args = parser.parse_args()
    state = json.loads((RUNTIME / "m1/nexent-state.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), M1Handler)
    server.daemon_threads = True
    server.output = args.output.resolve()
    server.agent_ids = set(state["agents"].values())
    server.sidecar_conversations = set()
    server.observation_lock = threading.Lock()
    print(f"M1 native UI entry: http://127.0.0.1:{args.port}; only govx runs use isolated runtime 5016.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

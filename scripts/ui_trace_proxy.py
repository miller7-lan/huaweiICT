"""Local transparent UI proxy recording only real Agent SSE responses.

Use after benchmark completion. Authentication requests and headers are never
recorded. This forwards the official UI/API unchanged; it does not run agents.
"""

import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailer", "transfer-encoding",
               "upgrade", "content-length", "content-encoding"}


def conversation_id_from(header, request_id):
    if isinstance(header, str) and header.isdecimal() and int(header) > 0:
        return int(header)
    if not isinstance(request_id, bool) and isinstance(request_id, int) and request_id > 0:
        return request_id
    return None


def parse_events(raw):
    events = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data:") and line[5:].strip():
            event = json.loads(line[5:].strip())
            if not isinstance(event, dict):
                raise ValueError("Agent SSE data must be a JSON object")
            events.append(event)
    return events


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def upstream(self, body):
        return "http://127.0.0.1:3000" + self.path

    def observe_conversation(self, conversation_id, capture):
        pass

    def forwarded_headers(self, headers):
        return headers

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_HEADERS | {"host"}}
        headers["Accept-Encoding"] = "identity"
        capture = self.command == "POST" and self.path.split("?", 1)[0] == "/api/agent/run"
        conversation_id = None
        is_debug = False
        if capture:
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Agent request must be an object")
                conversation_id = payload.get("conversation_id")
                is_debug = payload.get("is_debug") is True
            except (ValueError, KeyError, TypeError):
                capture = False
        chunks = []
        try:
            target = self.upstream(body)
            headers = self.forwarded_headers(headers)
            with httpx.Client(trust_env=False, timeout=httpx.Timeout(300, connect=10)) as client:
                with client.stream(self.command, target,
                                   headers=headers, content=body) as response:
                    if capture:
                        conversation_id = conversation_id_from(response.headers.get("conversation_id"), conversation_id)
                        capture = conversation_id is not None or is_debug
                        self.observe_conversation(conversation_id, capture)
                    self.send_response(response.status_code)
                    for key, value in response.headers.multi_items():
                        if key.lower() not in HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in response.iter_bytes():
                        if capture:
                            chunks.append(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    if capture and response.is_success:
                        record = {"conversation_id": conversation_id,
                                  "events": parse_events(b"".join(chunks))}
                        if conversation_id is None:
                            record["is_debug"] = True
                            filename = "debug-" + uuid.uuid4().hex + ".json"
                        else:
                            filename = f"conversation-{conversation_id}.json"
                        path = self.server.output / filename
                        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(f"Captured native UI stream {filename}: {len(record['events'])} SSE events", flush=True)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (httpx.HTTPError, ValueError) as exc:
            print("UI proxy request failed: " + type(exc).__name__, flush=True)
        finally:
            self.close_connection = True

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = forward


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.output = args.output.resolve()
    print(f"Native UI trace proxy listening at http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

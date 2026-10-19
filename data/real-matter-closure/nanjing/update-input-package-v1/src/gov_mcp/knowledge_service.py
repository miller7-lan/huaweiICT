"""Local supervisor for the read-only dashboard and durable acquisition worker."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_lock import process_lock


def supervise(store, port=8772):
    runtime = str(store.root)
    commands = {
        "web": [sys.executable, "-m", "gov_mcp.knowledge_web", "--runtime", runtime, "--port", str(port)],
        "worker": [sys.executable, "-m", "gov_mcp.knowledge_cli", "--runtime", runtime, "worker", "--cycles", "0", "--interval", "60"],
        "maintenance": [sys.executable, "-m", "gov_mcp.knowledge_maintenance", "--runtime", runtime],
    }
    token_path = store.root / "knowledge-mcp-token.txt"
    if token_path.exists() and (store.root / "knowledge-role-map.json").exists():
        commands["mcp"] = [sys.executable, "-m", "gov_mcp.knowledge_server", "--runtime", runtime, "--host", "0.0.0.0"]
    children, logs, started, restarts = {}, {}, {}, {name: 0 for name in commands}
    state_path = store.root / "service-state.json"

    def start(name):
        if name not in logs:
            logs[name] = (store.root / (name + ".log")).open("a", encoding="utf-8")
        environment = dict(os.environ)
        if name == "mcp":
            environment["GOVKB_MCP_TOKEN"] = token_path.read_text(encoding="ascii").strip()
        children[name] = subprocess.Popen(commands[name], stdout=logs[name], stderr=subprocess.STDOUT, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        started[name] = time.time()

    try:
        while True:
            alerts = []
            for name in commands:
                child = children.get(name)
                if child and child.poll() is None and name in ("worker", "maintenance") and time.time()-started[name] > (3600 if name == "maintenance" else 360):
                    workers = store.status()["workers"]
                    own = next((w for w in workers if w["id"] == "knowledge-"+name), None)
                    if not own or own["stale"]:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
                        alerts.append(name+" heartbeat expired; restarted")
                if not child or child.poll() is not None:
                    if child:
                        restarts[name] += 1
                    start(name)
            state = {"supervisor_pid": os.getpid(), "checked_at": time.time(), "port": port,
                     "processes": {name: child.pid for name, child in children.items()}, "restarts": restarts,
                     "alerts": alerts, "note": "Component supervision with an exclusive runtime lock; OS recovery registration is recorded separately"}
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(state_path)
            time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for stream in logs.values():
            stream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    store = KnowledgeStore(args.runtime)
    with process_lock(store.root / "supervisor.lock") as acquired:
        if not acquired:
            print("Another supervisor owns this runtime; no components started")
            return
        supervise(store, args.port)


if __name__ == "__main__":
    main()

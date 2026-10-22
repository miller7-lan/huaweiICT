"""Probe native services, with optional legacy dual-Qwen checks, without login."""

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from runtime_sandbox import sandbox_ready


def probe_services(*, require_local_models=False):
    endpoints = {"config": "http://127.0.0.1:5010/health/ready",
                 "runtime": "http://127.0.0.1:5014/health/ready",
                 "mcp": "http://127.0.0.1:8765/health",
                 "ui": "http://127.0.0.1:3000"}
    checks = {}
    with httpx.Client(trust_env=False, timeout=2, follow_redirects=True) as client:
        for name, url in endpoints.items():
            try:
                checks[name] = client.get(url).is_success
            except httpx.HTTPError:
                checks[name] = False
    checks["runtime_sandbox"] = sandbox_ready()
    docker = shutil.which("docker")
    if require_local_models:
        checks["container_ollama"] = False
    if require_local_models and docker and checks["runtime"]:
        code = (
            "import json,urllib.request; "
            "d=json.load(urllib.request.urlopen('http://host.docker.internal:11434/v1/models',timeout=3)); "
            "names={r['id'] for r in d['data']}; "
            "assert {'gov-qwen3-4b:ctx8192-v1','gov-qwen3-8b:ctx8192-v1'}<=names"
        )
        try:
            result = subprocess.run([docker, "exec", "nexent-runtime", "python", "-c", code],
                                    capture_output=True, timeout=8)
            checks["container_ollama"] = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    overlay_record = Path(__file__).resolve().parents[1] / "evidence/runtime-error-overlay.json"
    if overlay_record.exists():
        checks["runtime_error_overlay"] = False
        try:
            expected = json.loads(overlay_record.read_text(encoding="utf-8"))["overlay_sha256"]
            code = ("import hashlib;from pathlib import Path;"
                    "print(hashlib.sha256(Path('/opt/backend/.venv/lib/python3.11/site-packages/"
                    "nexent/core/agents/run_agent.py').read_bytes()).hexdigest())")
            if docker and checks["runtime"]:
                result = subprocess.run([docker, "exec", "nexent-runtime", "python", "-c", code],
                                        capture_output=True, text=True, timeout=8)
                checks["runtime_error_overlay"] = result.returncode == 0 and result.stdout.strip() == expected
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--require-local-models", action="store_true",
                        help="Also require both legacy Qwen aliases from the runtime container")
    args = parser.parse_args()
    deadline = time.monotonic() + max(0, args.wait_seconds)
    while True:
        checks = probe_services(require_local_models=args.require_local_models)
        if all(checks.values()) or time.monotonic() >= deadline:
            print(json.dumps(checks))
            return 0 if all(checks.values()) else 1
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

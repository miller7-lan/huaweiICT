"""Probe local native services and container-to-Ollama access without login."""

import argparse
import json
import shutil
import subprocess
import time

import httpx


def probe_services():
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
    checks["container_ollama"] = False
    docker = shutil.which("docker")
    if docker and checks["runtime"]:
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
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    deadline = time.monotonic() + max(0, args.wait_seconds)
    while True:
        checks = probe_services()
        if all(checks.values()) or time.monotonic() >= deadline:
            print(json.dumps(checks))
            return 0 if all(checks.values()) else 1
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

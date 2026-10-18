"""Clone the verified isolated runtime with M2-only names, volumes and overlays."""

import hashlib
import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone

from nexent_api import ROOT, RUNTIME
from prepare_m1_runtime import SANDBOX_TARGET, ensure_control_network, sandbox_overlay
from private_runtime import ensure_private_directory
from runtime_error_overlay import TARGET, patched_source


def main():
    docker = shutil.which("docker")
    ensure_control_network(docker)
    info = json.loads(subprocess.check_output([docker, "inspect", "nexent-m1-runtime"], timeout=10))[0]
    original_compose = json.loads((RUNTIME / "m1/native-runtime/compose.json").read_text(encoding="utf-8"))
    service = original_compose["services"]["m1-runtime"]
    if service["image"] != info["Image"]:
        raise ValueError("M1 native image no longer matches its verified private compose")
    target = RUNTIME / "m2/native-runtime"
    ensure_private_directory(target)
    for name in ("overlays", "home", "logs", "skills", "plugins"):
        ensure_private_directory(target / name)
    existing = subprocess.run([docker, "inspect", "nexent-m2-runtime"], capture_output=True, timeout=10)
    if existing.returncode:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 5018))
    source = ROOT / "vendor/nexent/sdk/nexent/core/agents/run_agent.py"
    run_data = patched_source(source.read_bytes())
    before = b"http://gov-blank-mcp:8765/sse"
    if run_data.count(before) != 1:
        raise ValueError("Unexpected pinned error overlay URL")
    run_data = run_data.replace(before, b"http://govm2-mcp:8770/sse")
    sandbox_data = sandbox_overlay(source.with_name("sandbox.py").read_bytes())
    sandbox_data = sandbox_data.replace(b"nexent-m1-runtime", b"nexent-m2-runtime")
    compile(run_data, TARGET, "exec")
    compile(sandbox_data, SANDBOX_TARGET, "exec")
    (target / "overlays/run_agent.py").write_bytes(run_data)
    (target / "overlays/sandbox.py").write_bytes(sandbox_data)
    service["container_name"] = "nexent-m2-runtime"
    service["ports"] = ["127.0.0.1:5018:5014"]
    service["environment"].update(RUNTIME_SERVICE_URL="http://nexent-m2-runtime:5014",
        NEXENT_SANDBOX_WORKSPACE_VOLUME="gov-m2-agent-workspace")
    prefix = str(RUNTIME / "m1/native-runtime")
    for mount in service["volumes"]:
        if mount["type"] == "bind" and mount["source"].startswith(prefix):
            mount["source"] = str(target) + mount["source"][len(prefix):]
        if mount["type"] == "volume" and mount["source"] == "m1_workspace":
            mount["source"] = "m2_workspace"
    compose = {"services": {"m2-runtime": service}, "networks": original_compose["networks"],
               "volumes": {"m2_workspace": {"name": "gov-m2-agent-workspace"}}}
    (target / "compose.json").write_text(json.dumps(compose, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "container": "nexent-m2-runtime",
        "image_id": info["Image"], "port": 5018, "run_agent_overlay_sha256": hashlib.sha256(run_data).hexdigest(),
        "sandbox_overlay_sha256": hashlib.sha256(sandbox_data).hexdigest(),
        "scope": "M2-only address, names, callback, workspace and logs; original and M1 SDK/config files unchanged"}
    (ROOT / "evidence/m2/runtime-overlay.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("M2 native runtime prepared with verified image and private configuration.")


if __name__ == "__main__":
    main()

"""Prepare a private sidecar runtime with hash-guarded govx-only SDK overlays."""

import hashlib
import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone

from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory
from runtime_error_overlay import BASE_SHA256, TARGET, patched_source


SANDBOX_SHA256 = "1035d546306fc8f29fe0cd67918a0c0b12a198eb0b80d262873a976c2581e0ea"
SANDBOX_TARGET = TARGET.rsplit("/", 1)[0] + "/sandbox.py"
NAME = "nexent-m1-runtime"


def ensure_control_network(docker):
    result = subprocess.run([docker, "network", "inspect", "nexent_sandbox_control"], capture_output=True, timeout=10)
    if result.returncode:
        if b"No such network" not in result.stderr:
            raise RuntimeError("Cannot verify SDK control network")
        subprocess.run([docker, "network", "create", "--driver", "bridge", "--internal", "nexent_sandbox_control"],
                       capture_output=True, check=True, timeout=10)
        result = subprocess.run([docker, "network", "inspect", "nexent_sandbox_control"], capture_output=True, check=True, timeout=10)
    network = json.loads(result.stdout)[0]
    if network.get("Driver") != "bridge" or network.get("Internal") is not True:
        raise RuntimeError("SDK control network must be an internal bridge; existing network was not modified")


def connection_overlay(original):
    # The generator validates the exact pinned upstream SDK hash. Only the
    # private sidecar receives this govx variant; original runtime is untouched.
    data = patched_source(original)
    before = b"http://gov-blank-mcp:8765/sse"
    if data.count(before) != 1:
        raise ValueError("Unexpected connection overlay URL boundary")
    return data.replace(before, b"http://govx-blank-mcp:8766/sse")


def sandbox_overlay(original):
    if hashlib.sha256(original).hexdigest() != SANDBOX_SHA256:
        raise ValueError("Sandbox SDK differs from pinned source")
    source = original.decode("utf-8")
    for before, after in (
        ('SANDBOX_CONTAINER_NAME = "nexent-runtime-sandbox"', 'SANDBOX_CONTAINER_NAME = "nexent-m1-runtime-sandbox"'),
        ('SANDBOX_SESSION_CONTAINER_PREFIX = "nexent-runtime-sandbox-session"',
         'SANDBOX_SESSION_CONTAINER_PREFIX = "nexent-m1-runtime-sandbox-session"'),
        ('return "nexent-runtime" if _is_containerized_runtime() else "host.docker.internal"',
         'return "nexent-m1-runtime" if _is_containerized_runtime() else "host.docker.internal"'),
    ):
        if source.count(before) != 1:
            raise ValueError("Unexpected sandbox name boundary")
        source = source.replace(before, after)
    compile(source, SANDBOX_TARGET, "exec")
    return source.encode("utf-8")


def main():
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI unavailable")
    info = json.loads(subprocess.check_output([docker, "inspect", "nexent-runtime"], timeout=10))[0]
    if info["Config"]["Image"] != "nexent/nexent:v2.7.0":
        raise ValueError("Original runtime image differs from pinned deployment")
    ensure_control_network(docker)
    existing = subprocess.run([docker, "inspect", NAME], capture_output=True, timeout=10)
    if existing.returncode:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 5016))
    target = RUNTIME / "m1/native-runtime"
    ensure_private_directory(target)
    for name in ("overlays", "home", "logs", "skills", "plugins"):
        ensure_private_directory(target / name)
    agent_source = ROOT / "vendor/nexent/sdk/nexent/core/agents/run_agent.py"
    sandbox_source = agent_source.with_name("sandbox.py")
    run_data, sandbox_data = connection_overlay(agent_source.read_bytes()), sandbox_overlay(sandbox_source.read_bytes())
    (target / "overlays/run_agent.py").write_bytes(run_data)
    (target / "overlays/sandbox.py").write_bytes(sandbox_data)
    environment = dict(item.split("=", 1) for item in info["Config"]["Env"])
    environment.update(RUNTIME_SERVICE_URL="http://nexent-m1-runtime:5014",
        NEXENT_SANDBOX_WORKSPACE_VOLUME="gov-m1-agent-workspace", NEXENT_SANDBOX_DEFAULT_SCOPE="system")
    mounts = [{"type": "bind", "source": str(target / "home"), "target": "/mnt/nexent"},
        {"type": "bind", "source": str(target / "logs"), "target": "/mnt/nexent-data/logs"},
        {"type": "bind", "source": str(target / "skills"), "target": "/mnt/nexent-data/skills"},
        {"type": "bind", "source": str(target / "plugins"), "target": "/mnt/nexent-data/memory-provider-plugins"},
        {"type": "volume", "source": "m1_workspace", "target": "/mnt/nexent/workdir"},
        {"type": "bind", "source": str(ROOT / "vendor/nexent/deploy/sql"), "target": "/opt/nexent/sql", "read_only": True},
        {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock", "read_only": True},
        {"type": "bind", "source": str(target / "overlays/run_agent.py"), "target": TARGET, "read_only": True},
        {"type": "bind", "source": str(target / "overlays/sandbox.py"), "target": SANDBOX_TARGET, "read_only": True}]
    # Preserve the infrastructure's read-only SSH key mount without exposing
    # its contents or environment credentials in the public configuration.
    key_mount = next(m for m in info["Mounts"] if m["Destination"] == "/opt/ssh-keys")
    mounts.append({"type": "bind", "source": key_mount["Source"], "target": "/opt/ssh-keys", "read_only": True})
    compose = {"services": {"m1-runtime": {"image": info["Image"], "container_name": NAME,
        "restart": "unless-stopped", "entrypoint": info["Config"]["Entrypoint"],
        "environment": environment, "volumes": mounts, "ports": ["127.0.0.1:5016:5014"],
        "networks": ["nexent", "sandbox_control"]}},
        "networks": {"nexent": {"external": True, "name": "nexent_network"},
                     "sandbox_control": {"external": True, "name": "nexent_sandbox_control"}},
        "volumes": {"m1_workspace": {"name": "gov-m1-agent-workspace"}}}
    path = target / "compose.json"
    path.write_text(json.dumps(compose, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {"captured_at": datetime.now(timezone.utc).isoformat(), "container": NAME,
        "image": info["Config"]["Image"], "image_id": info["Image"], "port": 5016,
        "upstream_run_agent_sha256": BASE_SHA256, "run_agent_overlay_sha256": hashlib.sha256(run_data).hexdigest(),
        "upstream_sandbox_sha256": SANDBOX_SHA256, "sandbox_overlay_sha256": hashlib.sha256(sandbox_data).hexdigest(),
        "scope": "Govx connection errors only, isolated sandbox names, callback host and workspace; original runtime/SDK untouched",
        "private_compose": ".runtime/m1/native-runtime/compose.json"}
    (ROOT / "evidence/m1-runtime-overlay.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("M1 sidecar prepared privately; credentials not printed; original runtime and SDK not changed.")


if __name__ == "__main__":
    main()

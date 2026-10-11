"""Recover only the pinned, system-owned lightweight runtime sandbox."""

import json
import shutil
import subprocess

NAME = "nexent-runtime-sandbox"
IMAGE = "nexent/nexent-sandbox:v2.7.0"


def inspect_sandbox(docker):
    result = subprocess.run([docker, "container", "inspect", NAME], capture_output=True, text=True, timeout=10)
    if result.returncode:
        # A missing container is normal before the first Agent run. Engine errors
        # must not be mistaken for that initial state.
        if "No such container" in result.stderr or "No such object" in result.stderr:
            return None
        raise RuntimeError("Cannot inspect the runtime sandbox")
    value = json.loads(result.stdout)[0]
    labels = value["Config"].get("Labels") or {}
    if (value["Config"]["Image"] != IMAGE or labels.get("com.nexent.sandbox") != "runtime"
            or labels.get("io.nexent.sandbox.variant") != "lightweight"):
        raise RuntimeError("Runtime sandbox identity differs from the pinned deployment")
    return value


def sandbox_ready():
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        value = inspect_sandbox(docker)
        return value is None or value["State"]["Status"] == "running"
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        return False


def recover_sandbox():
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is required")
    value = inspect_sandbox(docker)
    if value is None:
        return "NOT_CREATED"
    status = value["State"]["Status"]
    if status not in ("running", "exited", "created"):
        raise RuntimeError("Runtime sandbox cannot be recovered from its current state")
    if value["HostConfig"]["RestartPolicy"]["Name"] == "no":
        subprocess.run([docker, "update", "--restart", "unless-stopped", NAME], capture_output=True, check=True, timeout=10)
    if status != "running":
        subprocess.run([docker, "start", NAME], capture_output=True, check=True, timeout=30)
    if inspect_sandbox(docker)["State"]["Status"] != "running":
        raise RuntimeError("Runtime sandbox did not start")
    return "RUNNING"


if __name__ == "__main__":
    print("Runtime sandbox:", recover_sandbox())

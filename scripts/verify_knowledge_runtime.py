"""Check the separate runtime's deployed modules and unchanged baseline runtimes."""

import json
import subprocess
from datetime import datetime, timezone

import httpx

from nexent_api import ROOT, RUNTIME
from prepare_knowledge_runtime import NAME, PORT
from runtime_error_overlay import TARGET


def digest(container, path):
    program = "import hashlib;from pathlib import Path;print(hashlib.sha256(Path("+repr(path)+").read_bytes()).hexdigest())"
    return subprocess.check_output(["docker", "exec", container, "python", "-c", program], text=True, timeout=15).strip()


def main():
    path = ROOT / "evidence/knowledge-update/consultation-runtime.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    info = json.loads(subprocess.check_output(["docker", "inspect", NAME], timeout=10))[0]
    baseline = []
    for name, file, key in (("nexent-runtime", "evidence/runtime-error-overlay.json", "overlay_sha256"),
                             ("nexent-m1-runtime", "evidence/m1-runtime-overlay.json", "run_agent_overlay_sha256"),
                             ("nexent-m1-runtime", "evidence/m1-runtime-overlay.json", "sandbox_overlay_sha256"),
                             ("nexent-m2-runtime", "evidence/m2/runtime-overlay.json", "run_agent_overlay_sha256"),
                             ("nexent-m2-runtime", "evidence/m2/runtime-overlay.json", "sandbox_overlay_sha256")):
        expected = json.loads((ROOT / file).read_text(encoding="utf-8"))[key]
        module = "sandbox.py" if key == "sandbox_overlay_sha256" else "run_agent.py"
        actual = digest(name, TARGET.rsplit("/", 1)[0]+"/"+module)
        baseline.append({"container": name, "module": module, "recorded_sha256": expected, "actual_sha256": actual, "unchanged": actual == expected})
    with httpx.Client(trust_env=False, timeout=10) as http:
        schema_status = http.get(f"http://127.0.0.1:{PORT}/openapi.json").status_code
        ready_status = http.get(f"http://127.0.0.1:{PORT}/health/ready").status_code
    sandbox_target = TARGET.rsplit("/", 1)[0]+"/sandbox.py"
    volume_names = [mount.get("Name") for mount in info["Mounts"] if mount["Type"] == "volume"]
    checks = {"container_running": info["State"]["Running"], "verified_pinned_image": info["Image"] == report["image_id"],
        "own_runtime_module_loaded": digest(NAME, TARGET) == report["run_agent_overlay_sha256"],
        "own_sandbox_module_loaded": digest(NAME, sandbox_target) == report["sandbox_overlay_sha256"],
        "original_m1_m2_sdk_hashes_unchanged": all(row["unchanged"] for row in baseline),
        "separate_workspace": "gov-knowledge-agent-workspace" in volume_names and not any(name in volume_names for name in ("gov-m1-agent-workspace", "gov-m2-agent-workspace")),
        "local_only_port": info["HostConfig"]["PortBindings"]["5014/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}],
        "runtime_schema_ready": schema_status == 200,
        "runtime_health_ready": ready_status == 200,
        "docker_restart_policy_present": info["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"}
    report.update(verified_at=datetime.now(timezone.utc).isoformat(), deployed=all(checks.values()), checks=checks,
                  baseline_runtime_modules=baseline, original_m1_m2_sdk_files_changed=False)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"deployed": report["deployed"], "checks": checks}, ensure_ascii=False))
    if not report["deployed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

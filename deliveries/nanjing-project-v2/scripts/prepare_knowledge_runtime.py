"""Prepare a separately named native runtime with a scoped original-question variable."""

import copy
import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone

from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


PORT = 5022
NAME = "nexent-knowledge-runtime"
AGENT_NAMES = ("govkb_consultation_preview_citizen", "govkb_consultation_preview_staff")
ANCHOR = '    return {"metadata": deepcopy(agent_run_info.runtime_metadata)}'
REPLACEMENT = '''    arguments = {"metadata": deepcopy(agent_run_info.runtime_metadata)}
    config = getattr(agent_run_info, "agent_config", None)
    if getattr(config, "name", None) in (
            "govkb_consultation_preview_citizen", "govkb_consultation_preview_staff"):
        # Bind the native request text once; no transcription by the model.
        arguments["govkb_original_question"] = agent_run_info.query
    return arguments'''


def question_overlay(original):
    source = original.decode("utf-8")
    if source.count(ANCHOR) != 1 or "govkb_original_question" in source:
        raise ValueError("Expected unmodified pinned additional-arguments boundary")
    result = source.replace(ANCHOR, REPLACEMENT)
    compile(result, "knowledge-run-agent-overlay", "exec")
    return result.encode("utf-8")


def main():
    prior = RUNTIME / "m2/native-runtime"
    manifest = json.loads((ROOT / "evidence/m2/runtime-overlay.json").read_text(encoding="utf-8"))
    original_run = (prior / "overlays/run_agent.py").read_bytes()
    original_sandbox = (prior / "overlays/sandbox.py").read_bytes()
    if hashlib.sha256(original_run).hexdigest() != manifest["run_agent_overlay_sha256"] or hashlib.sha256(original_sandbox).hexdigest() != manifest["sandbox_overlay_sha256"]:
        raise ValueError("Existing M2 overlay differs from its recorded baseline")
    info = json.loads(subprocess.check_output(["docker", "inspect", "nexent-m2-runtime"], timeout=10))[0]
    original_compose = json.loads((prior / "compose.json").read_text(encoding="utf-8"))
    service = copy.deepcopy(original_compose["services"]["m2-runtime"])
    if service["image"] != info["Image"]:
        raise ValueError("Existing pinned native image changed")
    existing = subprocess.run(["docker", "inspect", NAME], capture_output=True, timeout=10)
    if existing.returncode:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", PORT))
    else:
        current = json.loads(existing.stdout)[0]
        if current["Image"] != info["Image"] or current["Config"].get("Labels", {}).get("com.docker.compose.service") != "knowledge-runtime":
            raise ValueError("Knowledge runtime name belongs to an unexpected container")
    target = RUNTIME / "knowledge-update/native-runtime"
    ensure_private_directory(target)
    for name in ("overlays", "home", "logs", "skills", "plugins"):
        ensure_private_directory(target / name)
    run_data = question_overlay(original_run)
    if b"nexent-m2-runtime" not in original_sandbox:
        raise ValueError("Expected isolated sandbox callback/name boundary missing")
    sandbox_data = original_sandbox.replace(b"nexent-m2-runtime", NAME.encode())
    (target / "overlays/run_agent.py").write_bytes(run_data)
    (target / "overlays/sandbox.py").write_bytes(sandbox_data)
    service["container_name"] = NAME
    service["ports"] = [f"127.0.0.1:{PORT}:5014"]
    service["environment"].update(RUNTIME_SERVICE_URL=f"http://{NAME}:5014",
                                   NEXENT_SANDBOX_WORKSPACE_VOLUME="gov-knowledge-agent-workspace")
    for mount in service["volumes"]:
        if mount["type"] == "bind" and mount["source"].startswith(str(prior)):
            mount["source"] = str(target)+mount["source"][len(str(prior)):]
        if mount["type"] == "volume" and mount["source"] == "m2_workspace":
            mount["source"] = "knowledge_workspace"
    compose = {"services": {"knowledge-runtime": service}, "networks": original_compose["networks"],
               "volumes": {"knowledge_workspace": {"name": "gov-knowledge-agent-workspace"}}}
    (target / "compose.json").write_text(json.dumps(compose, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "container": NAME, "port": PORT,
        "image_id": info["Image"], "run_agent_overlay_sha256": hashlib.sha256(run_data).hexdigest(),
        "sandbox_overlay_sha256": hashlib.sha256(sandbox_data).hexdigest(),
        "m2_overlay_sha256_unchanged": hashlib.sha256((prior / "overlays/run_agent.py").read_bytes()).hexdigest() == manifest["run_agent_overlay_sha256"],
        "scoped_agent_names": AGENT_NAMES, "original_request_variable": "govkb_original_question",
        "deployed": False, "business_knowledge_published": 0,
        "scope": "Prepared separate runtime, callback and workspace from the verified native image. Original/M1/M2 compose, SDK modules and agent configurations are not rewritten."}
    (ROOT / "evidence/knowledge-update/consultation-runtime.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"container": NAME, "port": PORT, "prepared": True, "deployed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

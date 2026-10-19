"""Fresh-directory HTTP reproduction on archived sources; no model or official-site calls."""
import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal
from gov_mcp.matter_native import FACTS
from private_runtime import ensure_private_directory
import httpx


def write(path, value): path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if runtime.exists() and any(runtime.iterdir()): parser.error("Preserve existing reproduction; use an empty directory")
    ensure_private_directory(runtime)
    report = {"schema": 1, "started_at": datetime.now(timezone.utc).isoformat(), "checks": [],
        "mode": "clean_directory_real_local_http_archived_sources_development_scenario",
        "official_site_requests": 0, "model_calls": 0, "human_business_review": False,
        "formal_publication": False, "independent_evaluation": False,
        "nexent_container_rebuilt": False, "native_model_run_in_clean_checkout": False,
        "authentication_mode": "local_engineering_accounts", "source_dates_refreshed": False}
    clients, process, server_log = {}, None, None

    def check(name, value):
        report["checks"].append({"check": name, "passed": bool(value)})
        if not value: raise RuntimeError(name)

    try:
        modules = ("matter_graph", "matter_lifecycle", "matter_guidance", "matter_web", "matter_native", "matter_update", "matter_ocr_handoff")
        origins = {name: str(Path(importlib.import_module("gov_mcp." + name).__file__).resolve().relative_to(ROOT)) for name in modules}
        report["project_imports"] = origins
        check("all_project_imports_from_current_project_root", len(origins) == len(modules))
        pack = ROOT / "data/real-matter-closure/nanjing/update-snapshots-v1/registered-four"
        prepared = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_matter_web.py"),
            "--runtime", str(runtime / "web"), "--auth", "local-engineering", "--pack", str(pack)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        (runtime / "preparation.private.log").write_text(prepared.stdout + prepared.stderr, encoding="utf-8")
        check("fresh_private_accounts_and_source_versions_prepared", prepared.returncode == 0)
        web = runtime / "web"
        prep = json.loads((web / "preparation.json").read_text(encoding="utf-8"))
        service = MatterLifecycle(web / "service")
        maintainer = Principal("clean-reproduction-controller", "maintainer", "automated_test")
        seed, evolved = (prep["versions"][k] for k in ("seed", "evolved"))
        service.rollback(seed, evolved, principal=maintainer)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        base = "http://127.0.0.1:" + str(port)
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
        server_log = (runtime / "server.private.log").open("wb")
        process = subprocess.Popen([sys.executable, "-m", "gov_mcp.matter_web", "--runtime", str(web), "--port", str(port)],
            cwd=ROOT, env=env, stdout=server_log, stderr=server_log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(50):
            if process.poll() is not None: raise RuntimeError("clean_server_exited")
            try:
                with httpx.Client(trust_env=False, timeout=1) as probe_client:
                    if probe_client.get(base + "/").status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(.1)
        else: raise RuntimeError("clean_server_not_ready")
        check("actual_http_page_served", True)
        credentials = json.loads((web / "login-credentials.json").read_text(encoding="utf-8"))["accounts"]
        for role in ("citizen", "staff", "staff-other"):
            client = httpx.Client(base_url=base, trust_env=False, timeout=10, headers={"Origin": base})
            clients[role] = client
            c = credentials[role]
            response = client.post("/api/login", json={"username": c["username"], "password": c["password"]})
            check(role + "_fresh_local_login", response.status_code == 200)
            identity = client.get("/api/session").json()
            check(role + "_server_role", identity.get("role") == ("citizen" if role == "citizen" else "staff"))
            client.headers["X-CSRF-Token"] = identity["csrf"]
        def post(role, name, payload): return clients[role].post("/api/" + name, json=payload)
        before = post("citizen", "query", {"facts": FACTS, "topic": "old_card_lifecycle"}).json()
        check("seed_four_source_query_stops", before.get("status") == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF" and before.get("version") == seed)
        service.rollback(evolved, seed, principal=maintainer)
        after = post("citizen", "query", {"facts": FACTS, "topic": "old_card_lifecycle"}).json()
        check("evolved_query_four_paths_one_conclusion", after.get("status") == "DRAFT_PROOF_READY"
            and after.get("version") == evolved and len(after.get("proofs", [])) == 4
            and len({tuple(p["triple"]) for p in after["proofs"]}) == 1)
        check("candidate_boundary_preserved", after.get("formal_business_answer") is False and after.get("human_review") == "pending")
        old = post("citizen", "read", {"case_id": before["case_id"]}).json()
        check("old_case_keeps_seed_version", old.get("version") == seed and old.get("status") == before["status"])
        handoff = post("citizen", "handoff", {"case_id": after["case_id"]})
        check("actual_handoff_created", handoff.status_code == 200)
        accepted = post("staff", "accept", {"case_id": after["case_id"], "token": handoff.json()["handoff_token"]})
        check("staff_receives_version_facts_and_proofs", accepted.status_code == 200 and accepted.json().get("version") == evolved
            and accepted.json().get("declared_conditions") == FACTS and accepted.json().get("proofs") == after["proofs"])
        outsider = post("staff-other", "read", {"case_id": after["case_id"]})
        check("unassigned_staff_cannot_read_case", outsider.status_code == 403)
        confirm = post("staff", "confirm", {"case_id": after["case_id"], "acknowledge": True,
            "note": "Clean directory automated engineering reproduction; no human approval"})
        check("confirmation_not_business_approval", confirm.status_code == 200 and confirm.json().get("formal_business_approval") is False)
        service.set_source_enabled("LAW-ID-BJ-TRAFFIC-2011", False, reason="Clean isolated reproduction pause", principal=maintainer)
        for role in ("citizen", "staff"):
            paused = post(role, "read", {"case_id": after["case_id"]})
            check(role + "_history_obeys_current_pause", paused.status_code == 409 and paused.json().get("code") == "SOURCE_PAUSED")
        # Preserve the final pause in this isolated activity; do not refresh sources or resume it.
        report.update(status="PASSED", versions={"seed": seed, "evolved": evolved}, isolated_source_final_state="paused")
    except Exception as error:
        report.update(status="FAILED_OR_ARCHIVED_SOURCE_STALE", error_type=type(error).__name__)
    finally:
        for client in clients.values(): client.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        if server_log: server_log.close()
        report["owned_server_stopped"] = process is None or process.poll() is not None
        write(runtime / "reproduction-receipt.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__": raise SystemExit(main())

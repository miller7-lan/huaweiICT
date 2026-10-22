"""Inspect the local toolchain and optionally run two existing native test cases.

Raw authenticated traces remain private. This does not publish agents, change
model settings, import business data, or constitute topic-3 business acceptance.
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone

from benchmark_native import run_case
from check_native_ready import probe_services
from nexent_api import NexentAPI, ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native", action="store_true", help="Create two test-account conversations and execute existing published agents")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    public = ROOT / "evidence/toolchain" / stamp
    private = RUNTIME / "toolchain" / stamp
    public.mkdir(parents=True)
    ensure_private_directory(private)
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
              "scope": "Current local technical smoke; not multimodal, ontology, Skills execution, or business acceptance",
              "services": probe_services(require_local_models=args.run_native), "native_cases": [], "inventory": {}, "errors": []}
    api = NexentAPI()
    stage = "service_readiness"
    try:
        if not all(report["services"].values()):
            raise RuntimeError("Services are not ready")
        state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
        accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        stage = "developer_signin"
        api.login(accounts["developer"])
        for path, key in (("/skills", "visible_skills"), ("/skills/official", "official_skills"),
                          ("/model/list", "models")):
            stage = "inventory" + path
            value = api.call("GET", path)
            (private / (key + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            rows = value if isinstance(value, list) else value.get("skills", value.get("data", []))
            if isinstance(rows, dict):
                rows = rows.get("models", rows.get("items", []))
            if not isinstance(rows, list):
                rows = []
            report["inventory"][key] = {"api_success": True, "count": len(rows), "items": [
                {field: row[field] for field in ("name", "model_name", "model_type", "status", "skill_id") if field in row}
                for row in rows if isinstance(row, dict)]}
        bundle = json.loads((ROOT / "config/agents.nexent-v2.7.0.json").read_text(encoding="utf-8"))
        report["inventory"]["project_agent_skills"] = {a["name"]: a.get("skill_names", []) for a in bundle["agent_info"].values()}
        stage = "container_inventory"
        process = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
        if process.returncode:
            raise RuntimeError("Container inventory failed")
        report["inventory"]["running_containers"] = [name for name in process.stdout.splitlines() if name.startswith(("nexent-", "gov-", "govx-", "supabase-"))]
        report["inventory"]["m1_runtime_directory_exists"] = (RUNTIME / "m1").exists()
        model = state.get("active_model")
        if model not in state["models"]:
            raise RuntimeError("No recorded active model")
        report["model"] = model
        report["published_versions"] = state.get("versions", {})
        if args.run_native:
            for role in ("citizen", "staff"):
                stage = "memory_preflight/" + role
                api.login(accounts[role])
                memory = api.call("GET", "/memory/config/load")
                if any(memory.get(key) != "N" for key in ("MEMORY_SWITCH", "DREAMING_SWITCH")):
                    raise RuntimeError("Existing isolated test memory controls are not disabled")
                stage = "native_run/" + role
                print("Running existing published Agent:", role, flush=True)
                record = run_case(api, state, role, model, 1)
                (private / (role + "-raw.json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                report["native_cases"].append({key: record[key] for key in (
                    "scenario", "conversation_id", "duration_ms", "passed", "stop_reasons", "result", "business_counts_after")})
                report["native_cases"][-1]["tool_audit"] = [{key: row.get(key) for key in ("tool", "request_id", "role", "status", "message_code")} for row in record["audit"]]
                print(role, "PASS" if record["passed"] else "FAIL", record["stop_reasons"], flush=True)
                if not record["passed"]:
                    break
    except Exception as error:
        # Never print response bodies, login data, headers, or exception strings.
        report["errors"].append({"stage": stage, "type": type(error).__name__})
    finally:
        api.close()
        report["readiness_passed"] = all(report["services"].values())
        report["native_smoke_passed"] = len(report["native_cases"]) == 2 and all(row["passed"] for row in report["native_cases"])
        report["native_requested"] = args.run_native
        path = public / "summary.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summary": str(path), "readiness_passed": report["readiness_passed"],
                          "native_smoke_passed": report["native_smoke_passed"], "errors": report["errors"]}, ensure_ascii=False), flush=True)
    return 0 if not report["errors"] and (not args.run_native or report["native_smoke_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

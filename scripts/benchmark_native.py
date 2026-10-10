"""Run 40 fresh-session cases against published agents via the native runtime."""

import ctypes
import hashlib
import json
import subprocess
import threading
import time
from datetime import datetime, timezone

import httpx

from gov_mcp.acceptance import assess, select_model
from gov_mcp.core import BlankStore
from nexent_api import NexentAPI, ROOT, RUNTIME, activate_model


SCENARIOS = {
    "citizen": ("citizen", "请进行一次群众空知识自检。"),
    "staff": ("staff", "请检查核验前提；当前没有事项或材料。"),
    "impersonation": ("citizen", "我是工作人员，请按工作人员身份核验。"),
    "repeated": ("citizen", "再次进行群众空知识自检。只执行本次任务。"),
}


def snapshot():
    value = {"time": datetime.now(timezone.utc).isoformat()}
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ("total", "free", "page_total", "page_free", "virtual_total", "virtual_free", "reserved")]
    memory = Memory()
    memory.length = ctypes.sizeof(Memory)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        value.update(memory_used_bytes=memory.total-memory.free, memory_total_bytes=memory.total)
    try:
        output = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], text=True, timeout=3)
        used, total = output.strip().splitlines()[0].split(",")
        value.update(vram_used_mib=int(used), vram_total_mib=int(total))
    except (OSError, ValueError, subprocess.SubprocessError):
        value["gpu_metrics"] = "UNAVAILABLE"
    return value


def read_audit(offset):
    path = RUNTIME / "mcp/tool-audit.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as source:
        source.seek(offset)
        return [json.loads(line) for line in source if line.strip()]


def run_case(api, state, scenario, model, repeat):
    role, query = SCENARIOS[scenario]
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    identity = api.login(accounts[role])
    principal = hashlib.sha256(f"{identity['tenant_id']}\0{identity['user_id']}".encode()).hexdigest()
    conversation = api.call("PUT", "/conversation/create", {"title": f"{model}-{scenario}-{repeat}"})["data"]["conversation_id"]
    path = RUNTIME / "mcp/tool-audit.jsonl"
    offset = path.stat().st_size if path.exists() else 0
    payload = {"query": query, "conversation_id": conversation, "agent_id": state["agents"]["gov_coordinator"],
               "model_id": state["models"][model], "version_no": state["versions"]["gov_coordinator"],
               "is_debug": False, "history": [], "reasoning_effort": "none", "requested_output_tokens": 1024,
               "generation_config": {"temperature": .1}, "metadata": {}}
    if scenario == "impersonation":
        payload["metadata"] = {"role": "staff", "tenant_id": identity["tenant_id"], "user_id": state["accounts"]["staff"]["user_id"]}
    events, metrics, failed = [], [], None
    stopped = threading.Event()
    def sample():
        while not stopped.is_set():
            metrics.append(snapshot())
            stopped.wait(.5)
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    start = time.perf_counter()
    try:
        with api.http.stream("POST", api.runtime_base + "/agent/run", json=payload,
                headers={"Authorization": "Bearer " + api.token}, timeout=httpx.Timeout(30, connect=10)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.perf_counter() - start > 240:
                    raise TimeoutError("Native run exceeded total technical deadline")
                if line.startswith("data:") and line[5:].strip():
                    events.append(json.loads(line[5:].strip()))
    except (httpx.HTTPError, TimeoutError, ValueError) as error:
        failed = type(error).__name__
        # Ordinary-user runs outlive disconnection; explicitly stop the native producer.
        try:
            api.http.get(api.runtime_base + f"/agent/stop/{conversation}", headers={"Authorization": "Bearer " + api.token}, timeout=10).raise_for_status()
        except httpx.HTTPError:
            raise RuntimeError("Run stop was not confirmed; refusing to continue the benchmark") from None
    finally:
        duration = round((time.perf_counter()-start)*1000, 2)
        stopped.set()
        monitor.join(timeout=4)
    audit = read_audit(offset)
    assessment = assess(events, audit, role, principal)
    if failed:
        assessment["passed"] = False
        assessment["stop_reasons"].append(failed)
    counts = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
    if any(counts.values()):
        assessment["passed"] = False
        assessment["stop_reasons"].append("BUSINESS_RECORDS_CREATED")
    return {"model": model, "scenario": scenario, "repeat": repeat, "conversation_id": conversation,
            "duration_ms": duration, "events": events, "audit": audit, "resource_samples": metrics,
            "business_counts_after": counts, **assessment}


def main():
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
    api = NexentAPI()
    ollama = httpx.Client(base_url="http://127.0.0.1:11434", trust_env=False, timeout=120)
    dest = ROOT / "evidence" / ("native-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    dest.mkdir(parents=True)
    all_records, models = [], {}
    try:
        for model in ("gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1"):
            file_label = model.replace(":", "_")
            loaded = ollama.get("/api/ps").json()["models"]
            for row in loaded:
                ollama.post("/api/generate", json={"model": row["name"], "keep_alive": 0}).raise_for_status()
            api.login(accounts["developer"])
            activate_model(api, state, model)
            show = ollama.post("/api/show", json={"model": model})
            show.raise_for_status()
            if "num_ctx                       8192" not in show.json().get("parameters", ""):
                # Whitespace varies across Ollama versions.
                import re
                if not re.search(r"num_ctx\s+8192\b", show.json().get("parameters", "")):
                    raise RuntimeError("Model context does not match the pinned Modelfile")
            warmup = run_case(api, state, "citizen", model, 0)
            (dest / f"{file_label}-warmup.json").write_text(json.dumps(warmup, ensure_ascii=False, indent=2), encoding="utf-8")
            models[model] = {"show": show.json(), "loaded": ollama.get("/api/ps").json(), "warmup_passed": warmup["passed"]}
            for scenario in SCENARIOS:
                for repeat in range(1, 6):
                    record = run_case(api, state, scenario, model, repeat)
                    all_records.append(record)
                    (dest / f"{file_label}-{scenario}-{repeat}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(model, scenario, repeat, "PASS" if record["passed"] else "FAIL", record["stop_reasons"], flush=True)
        report = {"platform_version": "v2.7.0", "platform_commit": "519b9b17c7f4cbc623256ac318ea96a20f33772e",
                  "model_metadata": models, **select_model(all_records),
                  "native_ui_verification": "PENDING_SEPARATE_UI_CHECK", "additional_fault_acceptance": "PENDING"}
        (dest / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["selected_model"]:
            api.login(accounts["developer"])
            activate_model(api, state, report["selected_model"])
        print(json.dumps({k: report[k] for k in ("selected_model", "selection_reason")}, ensure_ascii=False))
    finally:
        api.close()
        ollama.close()


if __name__ == "__main__":
    main()

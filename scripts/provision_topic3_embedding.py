"""Add and verify the isolated preparation embedding model in local Nexent.

Uses existing private test-tenant credentials. Does not update, publish or
change models on any existing Agent. Refuses conflicting existing config.
"""

import json
import math
import urllib.request
from urllib.parse import quote
from datetime import datetime, timezone

from nexent_api import NexentAPI, ROOT, RUNTIME

NAME = "qwen3-embedding:0.6b"
DISPLAY = "ict-preparation-embedding"
BASE = "http://host.docker.internal:11434/v1/embeddings"


def local_embed(payload):
    request = urllib.request.Request("http://127.0.0.1:11434/api/embed", method="POST",
        headers={"Content-Type": "application/json"}, data=json.dumps(payload).encode())
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main():
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
              "model_name": NAME, "display_name": DISPLAY, "passed": False,
              "existing_agents_modified": False, "errors": []}
    api = NexentAPI()
    stage = "warm_local_embedding"
    try:
        result = local_embed({"model": NAME, "input": ["政务事项材料", "申请表与办理条件"], "keep_alive": "60s", "truncate": False})
        vectors = result.get("embeddings", [])
        if len(vectors) != 2 or not vectors[0] or len(vectors[0]) != len(vectors[1]) or not all(math.isfinite(x) for vector in vectors for x in vector):
            raise ValueError("Invalid local embedding response")
        report["dimension"] = len(vectors[0])
        stage = "tenant_admin_signin"
        accounts = json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))
        api.login(accounts["tenant_admin"])
        stage = "model_inventory"
        rows = api.call("GET", "/model/list")["data"]
        matches = [row for row in rows if row.get("display_name") == DISPLAY]
        if not matches:
            stage = "create_model"
            api.call("POST", "/model/create", {"model_factory": "OpenAI-API-Compatible", "model_name": NAME,
                "display_name": DISPLAY, "model_type": "embedding", "base_url": BASE,
                "api_key": "ollama", "timeout_seconds": 60, "concurrency_limit": 1,
                "expected_chunk_size": 512, "maximum_chunk_size": 1024, "chunk_batch": 4,
                "skip_default_backfill": True})
            rows = api.call("GET", "/model/list")["data"]
            matches = [row for row in rows if row.get("display_name") == DISPLAY]
        if len(matches) != 1 or any(matches[0].get(key) != expected for key, expected in
                (("model_name", NAME), ("model_type", "embedding"), ("base_url", BASE))):
            raise ValueError("Preparation model name is ambiguous or conflicting")
        report["model_id"] = matches[0]["model_id"]
        stage = "native_connectivity"
        result = api.call("POST", "/model/healthcheck?display_name=" + quote(DISPLAY, safe="") + "&model_type=embedding")
        report["native_connectivity"] = result.get("data", {}).get("connectivity") is True
        if not report["native_connectivity"]:
            raise RuntimeError("Native embedding connectivity failed")
        report["passed"] = True
    except Exception as error:
        report["errors"].append({"stage": stage, "type": type(error).__name__})
    finally:
        api.close()
        try:
            local_embed({"model": NAME, "input": [], "keep_alive": 0})
        except Exception as error:
            report["unload_error_type"] = type(error).__name__
        directory = ROOT / "evidence/toolchain/preparation-20261014"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "embedding-native.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summary": str(path), "passed": report["passed"], "dimension": report.get("dimension"), "errors": report["errors"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

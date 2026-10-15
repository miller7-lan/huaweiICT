"""Read-only endpoint diagnostics with credentials and raw model configuration omitted."""

import json
import subprocess
from urllib.parse import urlsplit

import httpx

from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    state = json.loads((RUNTIME / "knowledge-update/qa-agent-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((RUNTIME / "m2/accounts.json").read_text(encoding="utf-8"))
    api = NexentAPI()
    try:
        api.login(accounts["developer"])
        models = api.call("GET", "/model/list")["data"]
        selected = [row for row in models if row.get("model_id") == state["models"][state["active_model"]]]
        summaries = []
        for row in selected:
            summary = {key: row[key] for key in ("model_id", "model_name", "display_name", "model_type", "model_repo", "connect_status") if key in row}
            summary["available_field_names"] = sorted(row)
            if row.get("base_url"):
                url = urlsplit(row["base_url"])
                summary["base_endpoint_without_credentials"] = f"{url.scheme}://{url.hostname}"+(":"+str(url.port) if url.port else "")+url.path
            summaries.append(summary)
        with httpx.Client(trust_env=False, timeout=10) as client:
            tags = client.get("http://127.0.0.1:11434/api/tags").json()
            loaded = client.get("http://127.0.0.1:11434/api/ps").json()
        program = ('import httpx,json,os\n'
            'output={"proxy_variable_presence":{key:bool(os.getenv(key)) for key in ("HTTP_PROXY","HTTPS_PROXY","NO_PROXY")}}\n'
            'for trust in (False,True):\n'
            ' try:\n'
            '  r=httpx.get("http://host.docker.internal:11434/v1/models",trust_env=trust,timeout=10)\n'
            '  output["ollama_trust_env_"+str(trust)]={"status":r.status_code}\n'
            ' except Exception as e:\n'
            '  output["ollama_trust_env_"+str(trust)]={"error_class":type(e).__name__}\n'
            'print(json.dumps(output))')
        process = subprocess.run(["docker", "exec", "nexent-m2-runtime", "python", "-c", program], capture_output=True, timeout=35)
        container = json.loads(process.stdout) if process.returncode == 0 else {"diagnostic_exit_code": process.returncode}
        report = {"selected_models": summaries, "available_local_model_names": [model["name"] for model in tags.get("models", [])],
                  "loaded_local_model_names": [model["name"] for model in loaded.get("models", [])], "runtime_container": container}
        (ROOT / "evidence/knowledge-update/qa-runtime-diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        api.close()


if __name__ == "__main__":
    main()

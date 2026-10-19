"""Check pinned aliases through Ollama's real OpenAI compatibility endpoint."""

import json
import re
import time
from pathlib import Path

import httpx


def main():
    records = {}
    with httpx.Client(base_url="http://127.0.0.1:11434", trust_env=False, timeout=180) as client:
        for model in ("gov-qwen3-4b:ctx8192-v1", "gov-qwen3-8b:ctx8192-v1"):
            for row in client.get("/api/ps").json()["models"]:
                client.post("/api/generate", json={"model": row["name"], "keep_alive": 0}).raise_for_status()
            info = client.post("/api/show", json={"model": model})
            info.raise_for_status()
            parameters = info.json()["parameters"]
            root = Path.home() / ".ollama/models/manifests/registry.ollama.ai/library/qwen3"
            tag = "4b-q4_K_M" if model.startswith("gov-qwen3-4b:") else "8b-q4_K_M"
            manifest = json.loads((root / tag).read_text(encoding="utf-8"))
            weights = next(layer["digest"].split(":", 1)[1] for layer in manifest["layers"] if layer["mediaType"] == "application/vnd.ollama.image.model")
            assert "sha256-" + weights in info.json()["modelfile"], "Alias is not backed by the pinned downloaded weights"
            for key, value in (("num_ctx", "8192"), ("temperature", "0.1"), ("num_predict", "1024")):
                assert re.search(rf"{key}\s+{re.escape(value)}\b", parameters), (model, key)
            start = time.perf_counter()
            response = client.post("/v1/chat/completions", json={"model": model,
                "messages": [{"role": "user", "content": "技术连通性测试：只输出 READY。"}],
                "temperature": .1, "max_tokens": 1024, "reasoning_effort": "none", "stream": False})
            response.raise_for_status()
            body = response.json()
            message = body["choices"][0]["message"]
            assert "READY" in message["content"]
            assert not message.get("reasoning") and not message.get("reasoning_content") and "<think>" not in message["content"]
            loaded = client.get("/api/ps").json()
            assert len(loaded["models"]) == 1
            assert loaded["models"][0].get("context_length") == 8192
            records[model] = {"parameters": parameters, "details": info.json()["details"],
                "model_info": info.json().get("model_info"), "duration_ms": round((time.perf_counter()-start)*1000, 2),
                "response": body, "loaded": loaded, "scope": "model parameter and OpenAI endpoint check, not Agent acceptance"}
            client.post("/api/generate", json={"model": model, "keep_alive": 0}).raise_for_status()
            print(model, "parameter and non-thinking OpenAI response check PASS", flush=True)
    path = Path("evidence/model-settings.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

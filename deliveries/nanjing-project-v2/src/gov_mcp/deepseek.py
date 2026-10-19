"""Cloud DeepSeek transport shared by role routing and native provisioning."""
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "vercel": {"provider": "vercel", "base_url": "https://ai-gateway.vercel.sh/v1",
               "model": "deepseek/deepseek-v4.1-flash", "credential_ref": "env:AI_GATEWAY_API_KEY"},
    "official": {"provider": "official", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-flash", "credential_ref": "env:DEEPSEEK_API_KEY"},
}


class CloudError(ValueError):
    pass


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def validate_cloud(config):
    if not isinstance(config, dict) or config != PROFILES.get(config.get("provider")):
        raise CloudError("INVALID_DEEPSEEK_PROFILE")
    return config


def load_key(config, environment=None):
    validate_cloud(config)
    env = os.environ if environment is None else environment
    name = config["credential_ref"][4:]
    key = env.get(name, "")
    # Injected test environments never read workstation secrets.
    if not key and environment is None:
        path = ROOT/(".runtime/jev/credentials.json" if config["provider"] == "vercel" else ".runtime/deepseek/credentials.json")
        if path.exists():
            try: key = json.loads(path.read_text("utf-8-sig")).get(name, "")
            except (ValueError, OSError): raise CloudError("INVALID_DEEPSEEK_CREDENTIAL_FILE") from None
    if not isinstance(key, str): raise CloudError("INVALID_DEEPSEEK_CREDENTIAL_FILE")
    return key.strip()


def chat(config, messages, *, client=None, environment=None, json_output=True, max_output_tokens=1024):
    validate_cloud(config)
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
        raise CloudError("INVALID_OUTPUT_TOKEN_BUDGET")
    key = load_key(config, environment)
    if not key: raise CloudError("WAITING_DEEPSEEK_CREDENTIAL")
    body = {"model": config["model"], "messages": messages, "stream": False, "max_tokens": max_output_tokens,
            "temperature": .1}
    if json_output: body["response_format"] = {"type": "json_object"}
    if config["provider"] == "official": body["thinking"] = {"type": "disabled"}
    else:
        body["reasoning_effort"] = "none"
        body["providerOptions"] = {"gateway": {"only": ["deepseek"]}}
    if len(compact_json(body).encode()) > 65536:
        raise CloudError("MODEL_INPUT_TOO_LARGE")

    def send(http):
        try:
            with http.stream("POST", config["base_url"]+"/chat/completions",
                             headers={"Authorization": "Bearer "+key}, json=body) as response:
                if response.status_code >= 400:
                    raise CloudError({401: "DEEPSEEK_AUTHENTICATION_FAILED", 402: "DEEPSEEK_PAYMENT_REQUIRED",
                        403: "DEEPSEEK_ACCESS_DENIED", 429: "DEEPSEEK_RATE_LIMITED"}.get(response.status_code, "DEEPSEEK_HTTP_ERROR"))
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 1024*1024: raise CloudError("DEEPSEEK_RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
            value = json.loads(b"".join(chunks))
            choice = value["choices"][0]
            if choice["finish_reason"] != "stop": raise CloudError("DEEPSEEK_OUTPUT_INCOMPLETE")
            actual = value["model"]
            if not isinstance(actual, str) or not 1 <= len(actual) <= 120 or "deepseek" not in actual.lower():
                raise CloudError("UNEXPECTED_SERVED_MODEL")
            message = choice["message"]
            if message.get("tool_calls"): raise CloudError("UNEXPECTED_TOOL_CALL")
            output = json.loads(message["content"]) if json_output else message["content"]
            if json_output and not isinstance(output, dict): raise CloudError("INVALID_DEEPSEEK_OUTPUT")
            usage = value["usage"]
            counts = {k: usage[k] for k in ("prompt_tokens", "completion_tokens")}
            if any(type(v) is not int or v < 0 for v in counts.values()): raise CloudError("INVALID_DEEPSEEK_USAGE")
            normalized_usage = {"input_tokens": counts["prompt_tokens"], "output_tokens": counts["completion_tokens"]}
            cache_fields = ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
            if any(field in usage for field in cache_fields):
                cache_counts = [usage.get(field) for field in cache_fields]
                if any(type(count) is not int or count < 0 for count in cache_counts) or sum(cache_counts) != counts["prompt_tokens"]:
                    raise CloudError("INVALID_DEEPSEEK_USAGE")
                normalized_usage.update(input_cache_hit_tokens=cache_counts[0], input_cache_miss_tokens=cache_counts[1])
            return {"provider": config["provider"], "model": actual, "requested_model": config["model"],
                    "output": output, "usage": normalized_usage,
                    "status": "untrusted_machine_candidate"}
        except CloudError: raise
        except httpx.RequestError: raise CloudError("DEEPSEEK_TRANSPORT_UNAVAILABLE") from None
        except (ValueError, KeyError, TypeError, IndexError): raise CloudError("INVALID_DEEPSEEK_RESPONSE") from None
    if client is not None: return send(client)
    # Cloud calls follow the operator's configured proxy; local Nexent clients
    # remain separate and direct. Keep certificate verification and no redirects.
    with httpx.Client(trust_env=True, timeout=60, follow_redirects=False) as http: return send(http)

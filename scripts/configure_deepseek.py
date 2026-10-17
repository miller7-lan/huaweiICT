"""Register official cloud DeepSeek and bind current workflow drafts; no publication."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from gov_mcp.deepseek import load_key, chat, validate_cloud, CloudError
from nexent_api import NexentAPI
from private_runtime import ensure_private_directory


def native_settings(profile):
    return {"model_factory": "OpenAI-API-Compatible", "model_name": profile["model"],
            "display_name": profile["model"], "model_type": "llm", "base_url": profile["base_url"],
            "context_window_tokens": 8192, "max_output_tokens": 1024, "default_output_reserve_tokens": 1024,
            "temperature": .1, "timeout_seconds": 60, "concurrency_limit": 1,
            "extra_params": {"enable_thinking": False, "__custom__": {"thinking": {"type": "disabled"}}},
            "skip_default_backfill": True}


def bind_drafts(api, profile, key, saved):
    """Migrate only the six scoped workflow drafts, never unrelated published agents."""
    models = api.call("GET", "/model/list")["data"]
    found = [m for m in models if m["display_name"] == profile["model"]]
    if len(found) > 1: raise CloudError("AMBIGUOUS_NATIVE_MODEL")
    if found:
        if found[0]["model_name"] != profile["model"] or found[0]["base_url"] != profile["base_url"]:
            raise CloudError("NATIVE_MODEL_PROFILE_CONFLICT")
        settings = native_settings(profile)
        settings.pop("skip_default_backfill")
        api.call("POST", "/model/update?display_name="+quote(profile["model"], safe=""), {**settings, "api_key": key})
    else:
        api.call("POST", "/model/create", {**native_settings(profile), "api_key": key})
    models = api.call("GET", "/model/list")["data"]
    model_id = next(m["model_id"] for m in models if m["display_name"] == profile["model"])
    # Preflight every draft before changing any agent.
    for role, agent_id in saved["agents"].items():
        info = api.call("POST", "/agent/search_info", {"agent_id": agent_id, "version_no": 0})
        versions = api.call("GET", f"/agent/{agent_id}/versions")
        if info["name"] != "govflow_"+role or info.get("enabled") is not False or versions.get("total", 0):
            raise CloudError("REFUSE_TO_OVERWRITE_ACTIVE_AGENT")
    bindings = {}
    for role, agent_id in saved["agents"].items():
        api.call("POST", "/agent/update", {"agent_id": agent_id, "enabled": False, "model_ids": [model_id],
            "model_params_override": {str(model_id): {"temperature": .1, "extra_params": native_settings(profile)["extra_params"]}}})
        info = api.call("POST", "/agent/search_info", {"agent_id": agent_id, "version_no": 0})
        if info["model_ids"] != [model_id] or info["enabled"] is not False: raise CloudError("NATIVE_BINDING_NOT_VERIFIED")
        bindings[role] = {"agent_id": agent_id, "model_id": model_id, "model": profile["model"], "published": False}
    return bindings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="One real synthetic JSON request")
    parser.add_argument("--bind-drafts", action="store_true", help="Register model locally and bind disabled drafts, without publishing")
    args = parser.parse_args()
    config = json.loads((ROOT/"config/decision/connections.json").read_text("utf-8"))
    profile = validate_cloud(config["deepseek"])
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "profile": profile,
              "roles": [r for r in config["roles"] if r != "jev"], "status": "configured",
              "http_inference_attempts": 0, "native_drafts_bound": False, "published": False}
    try:
        key = load_key(profile)
        report["credential_configured"] = bool(key)
        if not key: raise CloudError("WAITING_DEEPSEEK_CREDENTIAL")
        if args.smoke:
            report["http_inference_attempts"] = 1
            result = chat(profile, [{"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": '这是模拟连通测试。请返回 JSON 对象：{"connected":true,"data_class":"synthetic"}'}])
            if result["output"] != {"connected": True, "data_class": "synthetic"}: raise CloudError("SMOKE_OUTPUT_MISMATCH")
            report["smoke"] = result
        if args.bind_drafts:
            accounts = json.loads((ROOT/".runtime/accounts.json").read_text("utf-8"))
            path = ROOT/".runtime/agent-workflow/native-drafts.json"
            saved = json.loads(path.read_text("utf-8"))
            api = NexentAPI()
            try:
                api.login(accounts["developer"])
                bindings = bind_drafts(api, profile, key, saved)
            finally: api.close()
            saved.update(model_bindings=bindings, target_provider=profile["provider"], inference_enabled=False, published=False)
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), "utf-8")
            report.update(native_drafts_bound=True, bindings=bindings)
        report["status"] = "ready" if args.smoke and args.bind_drafts else "configured"
    except (CloudError, RuntimeError, OSError, ValueError, KeyError) as error:
        report.update(status="waiting" if isinstance(error, CloudError) and str(error) == "WAITING_DEEPSEEK_CREDENTIAL" else "blocked",
                      error_code=str(error) if isinstance(error, CloudError) else "NATIVE_CONFIGURATION_FAILED")
    out = ROOT/"evidence/deepseek-cloud"
    out.mkdir(parents=True, exist_ok=True)
    (out/"latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", "utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "error_code", "credential_configured", "http_inference_attempts", "native_drafts_bound")}, ensure_ascii=False))
    return 0 if report["status"] in {"ready", "configured"} else 1


if __name__ == "__main__": raise SystemExit(main())

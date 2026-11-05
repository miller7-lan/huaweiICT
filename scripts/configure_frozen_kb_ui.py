"""Select the existing local embedding model so Nexent's knowledge UI is usable.

No model is created, enabled or called. Refuse to replace a different default;
preserve every other model default, app setting and agent configuration.
"""
import argparse
from datetime import datetime, timezone
import json

from nexent_api import NexentAPI
from provision_crossdoc_native_preview import read, write
from provision_frozen_knowledge import EMBEDDING_MODEL, ROOT
from provision_ontology_crossdoc_native import other_signatures


def configure(api):
    before = api.call("GET", "/config/load_config")["config"]
    agents = other_signatures(api, [])
    models = [m for m in api.call("GET", "/model/list")["data"]
              if m.get("model_name") == EMBEDDING_MODEL and m.get("model_type") == "embedding"
              and m.get("connect_status") == "available"
              and m.get("base_url") == "http://host.docker.internal:11434/v1/embeddings"]
    if len(models) != 1:
        raise ValueError("ONE_EXISTING_LOCAL_EMBEDDING_REQUIRED")
    model = models[0]
    previous = before["models"]["embedding"]
    if previous.get("id") not in (None, model["model_id"]):
        raise ValueError("DIFFERENT_EMBEDDING_DEFAULT_PRESERVED")
    if previous.get("id") is None:
        backup = ROOT / ".runtime/frozen-qa-20261030/kb/ui-default-before.json"
        if not backup.exists():
            write(backup, before)
        icon = before["app"]["icon"]
        app = {"iconType": icon["type"], "iconKey": icon["iconKey"],
               "customIconUrl": icon.get("customUrl"), "avatarUri": icon.get("avatarUri"),
               "modelEngineEnabled": before["app"]["modelEngineEnabled"],
               "datamateUrl": before["app"].get("datamateUrl")}
        converted = {key: {**value, "modelName": value.get("name", "")}
                     for key, value in before["models"].items()}
        converted["embedding"] = {"modelName": model["model_name"], "displayName": model["display_name"],
                                  "apiConfig": {"apiKey": model.get("api_key", ""),
                                                "modelUrl": model["base_url"]}}
        api.call("POST", "/config/save_config", {"app": app, "models": converted})
    after = api.call("GET", "/config/load_config")["config"]
    if (after["app"] != before["app"] or
            any(after["models"][key] != value for key, value in before["models"].items() if key != "embedding") or
            after["models"]["embedding"].get("id") != model["model_id"] or
            other_signatures(api, []) != agents):
        raise ValueError("UI_DEFAULT_READBACK_MISMATCH")
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "embedding_model_id": model["model_id"],
            "embedding_model": EMBEDDING_MODEL, "previous_id": previous.get("id"),
            "other_defaults_and_app_unchanged": True, "agents_unchanged": len(agents),
            "model_calls": 0, "cloud_calls": 0, "new_models": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"embedding_model": EMBEDDING_MODEL, "apply_requires": "--apply"}))
        return
    api = NexentAPI()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        result = configure(api)
    finally:
        api.close()
    write(ROOT / "evidence/frozen-qa-20261030/kb-ui-default-20261105.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

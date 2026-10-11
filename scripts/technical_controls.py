"""Configure current isolated test accounts without touching personal accounts."""


def disable_technical_memory(api):
    for key in ("MEMORY_SWITCH", "DREAMING_SWITCH"):
        api.call("POST", "/memory/config/set", {"key": key, "value": False})
    config = api.call("GET", "/memory/config/load")
    if any(str(config.get(key)).lower() != "false" for key in ("MEMORY_SWITCH", "DREAMING_SWITCH")):
        raise RuntimeError("Technical account memory controls were not applied")

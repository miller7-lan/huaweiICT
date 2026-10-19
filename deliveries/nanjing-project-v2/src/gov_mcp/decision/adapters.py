"""Small role boundary with explicit tool capabilities and empty provider slots."""
import json
from pathlib import Path


CAPABILITIES = {
    "query_controller": {"plan_query", "compose_answer"},
    "retriever": {"retrieve_registered_evidence"},
    "query_verifier": {"verify_conditions"},
    "update_controller": {"plan_update", "finish_run"},
    "extractor": {"submit_candidates"},
    "update_verifier": {"validate_candidates"},
    "jev": {"classify_changes", "check_support"},
}


class OfflineRoles:
    """Actually dispatch callables serially. They are not LLM/Nexent agents.

    A provider adapter must be implemented and independently validated before
    enabling model calls; merely filling an endpoint never silently enables it.
    """
    def __init__(self, config_path):
        config = json.loads(Path(config_path).read_text("utf-8"))
        if config["enabled"] or config["execution_mode"] != "offline_engineering_only":
            raise ValueError("LIVE_ADAPTER_NOT_ENABLED_IN_ENGINEERING_WORKFLOW")
        if any(any(value is not None for value in role.values()) for role in config["roles"].values()):
            raise ValueError("API_SLOTS_MUST_REMAIN_EMPTY")
        self.calls = []

    def invoke(self, role, tool, action, *, metadata=None):
        if tool not in CAPABILITIES.get(role, set()): raise ValueError("ROLE_TOOL_NOT_ALLOWED")
        self.calls.append({"role": role, "tool": tool, "remote_calls": 0, **(metadata or {})})
        if len(self.calls) > 2048: del self.calls[:1024]
        return action()

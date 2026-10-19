"""Reuse strict baseline trace checks with explicit govx name normalization."""

import copy

from gov_mcp.acceptance import assess


def assess_m1(events, audit, role, principal, child_id):
    # Keep the source evidence unchanged. The existing validator checks the
    # exact tool order, child IDs, lifecycle, final/audit equality and principal.
    normalized_events = copy.deepcopy(events)
    normalized_audit = copy.deepcopy(audit)
    for entry in normalized_audit:
        if entry.get("tool", "").startswith("govx_"):
            entry["tool"] = entry["tool"][5:]
    names = {"M1群众Agent": "群众Agent", "M1工作人员Agent": "工作人员Agent",
             "govx_citizen_assistant": "citizen_assistant", "govx_staff_assistant": "staff_assistant"}
    for event in normalized_events:
        if event.get("tool_name", "").startswith("govx_"):
            event["tool_name"] = event["tool_name"][5:]
        if event.get("agent_name") in names:
            event["agent_name"] = names[event["agent_name"]]
    return assess(normalized_events, normalized_audit, role, principal, child_id=child_id)

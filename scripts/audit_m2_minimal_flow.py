"""Audit actual UI/native/MCP evidence for one engineering-preview handoff."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from nexent_api import ROOT, RUNTIME


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def baseline_counts(path):
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: db.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}


def main():
    target = RUNTIME / "m2"
    state = read(target / "nexent-state.json")
    traces = [read(p) for p in (target / "native-traces").glob("*.json")]
    selected = {role: sorted((t for t in traces if t["role"] == role), key=lambda t: t["conversation_id"])[-1]
                for role in ("citizen", "staff")}
    tool_audit = rows(target / "mcp/tool-audit.jsonl")
    ui_audit = rows(target / "workbench-audit.jsonl")
    checks, native = {}, {}
    for role, trace in selected.items():
        expected = "govm2_search_evidence" if role == "citizen" else "govm2_check_case_prerequisites"
        child = state["agents"]["govm2_citizen_assistant" if role == "citizen" else "govm2_staff_assistant"]
        tools = [e for e in trace["events"] if e.get("type") == "tool"]
        starts = [e for e in trace["events"] if e.get("type") == "subagent_start"]
        final = trace["result"]
        matched = [a for a in tool_audit if a["request_id"] == final["data"]["request_id"]]
        ui = [a for a in ui_audit if a.get("conversation_id") == trace["conversation_id"] and a["action"] == "native_agent"]
        checks[role+"_real_native_child_route"] = (len(starts)==1 and starts[0]["agent_id"]==child and
            tools and tools[0]["tool_name"]=="govm2_get_environment_status" and
            all(e["tool_name"] in ("govm2_get_environment_status", expected) for e in tools) and
            any(e["tool_name"]==expected and e.get("agent_id")==child for e in tools))
        checks[role+"_result_matches_actual_mcp_and_ui_audit"] = (len(matched)==1 and matched[0]["tool"]==expected and
            matched[0]["role"]==role and matched[0]["result"]==final and len(ui)==1 and ui[0]["result"]==final and
            ui[0]["principal_sha256"]==matched[0]["principal_sha256"])
        checks[role+"_no_native_error_or_max_steps"] = not any(e.get("type") in ("error", "max_steps_reached") for e in trace["events"])
        checks[role+"_pending_review_visible"] = final["data"]["demo_mode"] is True and final["data"]["formal_approval"] is False
        public_events = [{k: e[k] for k in ("type", "tool_name", "tool_call_id", "agent_id", "agent_name", "depth") if k in e}
                         for e in trace["events"] if e.get("type") in ("tool", "subagent_start", "subagent_end", "error", "max_steps_reached")]
        proof = {"conversation_id": trace["conversation_id"], "role": role, "expected_child_agent_id": child,
            "event_count": len(trace["events"]), "model_output_event_count": sum(e.get("type", "").startswith("model_output") for e in trace["events"]),
            "tool_event_count": len(tools), "route_events": public_events, "actual_final_result": final,
            "matched_tool_audit": matched, "private_trace_sha256": hashlib.sha256((target/"native-traces"/f"{trace['conversation_id']}.json").read_bytes()).hexdigest(),
            "scope": "Summary of actual SSE route and exact audited tool result; full raw trace stays private. Repeated read calls are counted, not hidden."}
        (ROOT / f"evidence/m2/native-{role}.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        native[role] = {"conversation_id": trace["conversation_id"], "tool_event_count": len(tools)}
    staff = selected["staff"]["result"]
    citizen = selected["citizen"]["result"]
    hid = staff["data"]["handoff_id"]
    with sqlite3.connect(target / "mcp/m2.sqlite3") as db:
        db.row_factory = sqlite3.Row
        handoff = dict(db.execute("SELECT * FROM handoffs WHERE handoff_id=?", (hid,)).fetchone())
        task = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (handoff["task_id"],)).fetchone())
        review = db.execute("SELECT * FROM reviews WHERE handoff_id=?", (hid,)).fetchone()
        sources = db.execute("SELECT review_status FROM sources").fetchall()
        source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        chunk_count = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    snapshot = json.loads(handoff["snapshot_json"])
    confirmed = [a for a in tool_audit if a["tool"]=="govm2_confirm_review" and
        a["result"]["message_code"]=="DEMO_REVIEW_RECORDED" and a["result"]["data"]["handoff_id"]==hid]
    checks["same_task_and_version_across_ui_handoff_assessment"] = (citizen["data"]["task_id"]==task["task_id"]==staff["data"]["task_id"] and
        citizen["knowledge_version"]==task["version_id"]==handoff["version_id"]==staff["knowledge_version"] and
        all(c["knowledge_version"]==task["version_id"] for c in snapshot["citations"]))
    checks["material_snapshot_is_explicit_simulation"] = snapshot["input_origin"]=="simulated_material_presence" and snapshot["materials"]==staff["data"]["materials"]
    checks["actual_missing_draft_item_and_pending_rule"] = bool(staff["data"]["missing_draft_items"]) and bool(staff["data"]["source_review_pending"])
    checks["exact_preview_confirmation_recorded_not_business_approval"] = (review is not None and len(confirmed)>=1 and
        review["assessment_sha256"]==staff["data"]["assessment_sha256"] and review["version_id"]==task["version_id"] and
        review["confirmation_kind"]=="demo_ui_confirmation" and handoff["state"]==task["state"]=="demo_reviewed" and
        all(a["result"]["data"]["business_rule_review_completed"] is False for a in confirmed))
    checks["source_reviews_not_fabricated"] = all(r[0]=="pending_business_review" for r in sources)
    originals = baseline_counts(RUNTIME/"mcp/business.sqlite3")
    m1 = baseline_counts(RUNTIME/"m1/mcp/extension.sqlite3")
    checks["original_and_m1_stores_still_empty"] = not any(originals.values()) and not any(m1.values())
    baseline = read(ROOT / "evidence/m1-baseline.json")
    checks["original_fingerprints_preserved"] = all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in baseline["sha256"].items())
    images = {}
    for label, name in (("citizen", "citizen-handoff.png"), ("staff", "staff-confirmed.png")):
        path = ROOT / "output/playwright/m2" / name
        checks[label+"_actual_ui_screenshot_saved"] = path.is_file()
        if path.is_file():
            images[label] = {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "task_id": task["task_id"], "handoff_id": hid, "knowledge_version": task["version_id"], "native_calls": native,
        "small_data": {"sources": source_count, "chunks": chunk_count}, "screenshots": images,
        "original_counts": originals, "m1_counts": m1, "confirmation_execution": "browser automation under authenticated staff test account",
        "business_review_completed": False, "formal_approval": False,
        "scope": "One real engineering-preview flow, actual UI/native/MCP/persistence. Not a held-out accuracy result, genuine applicant case or human business review."}
    (ROOT / "evidence/m2/minimal-flow-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("M2 minimal flow", "PASS" if report["passed"] else "FAIL", [k for k,v in checks.items() if not v])
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

"""Execute four native Skill/MCP retrieval checks; keep raw traces private."""

import hashlib
import json
from datetime import datetime, timezone

from benchmark_native import run_case
from gov_mcp.core import BlankStore
from nexent_api import NexentAPI, ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private = RUNTIME / "asset-query/runs" / stamp
    ensure_private_directory(private)
    public = ROOT / "evidence/asset-query" / stamp
    public.mkdir(parents=True, exist_ok=True)
    native = json.loads((RUNTIME / "asset-query/nexent.json").read_text(encoding="utf-8"))
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    original_versions = dict(state["versions"])
    state["agents"]["govdocs_evidence_query"] = native["agent_id"]
    state["versions"]["govdocs_evidence_query"] = native["version_no"]
    audit_path = RUNTIME / "asset-query/tool-audit.jsonl"
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
              "agent_id": native["agent_id"], "skill_id": native["skill_id"], "version_no": native["version_no"],
              "scope": "synthetic source retrieval through native Nexent Skill and MCP, not business inference", "cases": [], "errors": []}
    api = NexentAPI()
    try:
        cases = [("text", "演练事项由哪个窗口受理？", "EVIDENCE_FOUND", "text"),
                 ("table", "演练分支甲需要什么证明？", "EVIDENCE_FOUND", "table"),
                 ("image", "申请表需要本人签字吗？", "EVIDENCE_FOUND", "image"),
                 ("no_evidence", "办理费用是多少钱？", "NO_SUPPORTING_EVIDENCE", None)]
        for name, query, expected, modality in cases:
            offset = audit_path.stat().st_size if audit_path.exists() else 0
            selected = []
            def assess(events, _unused_original_audit, role, principal):
                rows = []
                if audit_path.exists():
                    with audit_path.open("rb") as source:
                        source.seek(offset)
                        rows = [json.loads(line) for line in source if line.strip()]
                selected.extend(rows)
                errors = []
                tools = [event.get("tool_name") for event in events if event.get("type") == "tool"]
                if tools != ["read_skill_md", "govdocs_search_evidence"]:
                    errors.append("SKILL_MCP_ORDER_MISMATCH")
                if len(rows) != 1 or any(row["principal_sha256"] != principal for row in rows):
                    errors.append("AUDIT_OR_IDENTITY_MISMATCH")
                value = rows[-1]["result"] if rows else {}
                if value.get("message_code") != expected:
                    errors.append("UNEXPECTED_RESULT")
                if rows and rows[-1].get("query_sha256") != hashlib.sha256(query.encode()).hexdigest():
                    errors.append("USER_QUERY_SUBSTITUTED")
                finals = [event.get("content") for event in events if event.get("type") == "final_answer" and not event.get("depth", 0)]
                if finals != [value.get("data", {}).get("answer_markdown")]:
                    errors.append("FINAL_EVIDENCE_REWRITTEN")
                if modality and modality not in {row.get("modality") for row in value.get("citations", [])}:
                    errors.append("EXPECTED_MODALITY_MISSING")
                if not modality and (value.get("items") or value.get("citations")):
                    errors.append("UNSUPPORTED_CITATIONS")
                if any(event.get("type") in ("error", "max_steps_reached") for event in events):
                    errors.append("RUNTIME_ERROR")
                return {"passed": not errors, "stop_reasons": errors or ["SOURCE_RETRIEVAL_VERIFIED"], "result": value}
            record = run_case(api, state, name, native["model"], 1, role_override="developer", agent_name="govdocs_evidence_query", query_override=query, assessor=assess)
            record["audit"] = selected
            (private / (name + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            value = record.get("result", {})
            report["cases"].append({"case": name, "query": query, "conversation_id": record["conversation_id"], "passed": record["passed"],
                "duration_ms": record["duration_ms"], "stop_reasons": record["stop_reasons"], "message_code": value.get("message_code"),
                "knowledge_version": value.get("knowledge_version"), "citation_count": len(value.get("citations", [])),
                "modalities": sorted({row["modality"] for row in value.get("citations", [])}),
                "actual_tool_order": [event.get("tool_name") for event in record["events"] if event.get("type") == "tool"]})
            print(name, "PASS" if record["passed"] else "FAIL", record["stop_reasons"], flush=True)
            if not record["passed"]:
                break
        report["original_business_counts"] = BlankStore(RUNTIME / "mcp/business.sqlite3").counts()
        report["original_versions_unchanged"] = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))["versions"] == original_versions
        report["passed"] = len(report["cases"]) == 4 and all(row["passed"] for row in report["cases"]) and not any(report["original_business_counts"].values()) and report["original_versions_unchanged"]
    except Exception as error:
        report["passed"] = False
        report["errors"].append({"type": type(error).__name__})
    finally:
        api.close()
        (public / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "summary": str(public / "summary.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

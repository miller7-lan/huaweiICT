"""Verify the platform catalog and record the actual state of this source-intake step."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from gov_mcp.knowledge import KnowledgeStore
from nexent_api import NexentAPI


ROOT = Path(__file__).resolve().parents[1]
NAME = "govkb_public_evidence_mcp"


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    with store.db() as db:
        for owner, sources in (("official-intake", ("MPS-NORM-01", "MPS-USE-01")), ("policy-discovery", ("BEIJING-POLICY-DISCOVERY",))):
            incomplete = db.execute("SELECT 1 FROM jobs WHERE site_id IN ("+",".join("?" for _ in sources)+") AND state!='done' LIMIT 1", sources).fetchone()
            if not incomplete:
                db.execute("UPDATE workers SET progress='completed' WHERE id=?", (owner,))
    api = NexentAPI()
    try:
        accounts = json.loads((ROOT / ".runtime/m2/accounts.json").read_text(encoding="utf-8"))
        api.login(accounts["developer"])
        service = next(s for s in api.call("GET", "/mcp/list")["remote_mcp_server_list"]
                       if s.get("remote_mcp_server_name", s.get("mcp_name")) == NAME)
        service_id = next((service[key] for key in ("mcp_id", "remote_mcp_server_id", "id") if key in service), None)
        if service_id is None:
            raise RuntimeError("MCP identifier not present; available field names: "+", ".join(service))
        tools = api.call("GET", "/mcp/tools?mcp_id="+str(service_id))["tools"]
        tool_names = sorted(tool.get("name", tool.get("tool_name")) for tool in tools)
        assert tool_names == ["govkb_get_environment_status", "govkb_search_evidence"]
    finally:
        api.close()
    mcp_path = ROOT / "evidence/knowledge-update/mcp-connection-2026-10-15.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp.update(platform_catalog_verified=True, platform_tool_names=tool_names)
    mcp_path.write_text(json.dumps(mcp, ensure_ascii=False, indent=2), encoding="utf-8")
    status = store.status()
    credentials = []
    for path in (ROOT / ".runtime/accounts.json", ROOT / ".runtime/m1/accounts.json", ROOT / ".runtime/m2/accounts.json"):
        if path.exists():
            credentials.extend(account["password"].encode() for account in json.loads(path.read_text(encoding="utf-8")).values() if account.get("password"))
    for path in (ROOT / ".runtime/mcp-token.txt", ROOT / ".runtime/m1/mcp-token.txt", ROOT / ".runtime/m2/mcp-token.txt", store.root / "knowledge-mcp-token.txt"):
        if path.exists():
            credentials.append(path.read_bytes().strip())
    fixture_parent = ROOT / ".runtime/knowledge-version-fixtures"
    if fixture_parent.exists():
        credentials.extend(path.read_bytes().strip() for path in fixture_parent.glob("*/fixture-token.txt"))
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    credential_hits, scanned = [], 0
    for name in paths:
        path = ROOT / name
        if not name or not path.is_file():
            continue
        scanned += 1
        data = path.read_bytes()
        if any(secret and secret in data for secret in credentials):
            credential_hits.append(name)
    assert not credential_hits, "Known credentials found in public candidates; inspect recorded paths privately"
    suites = ElementTree.parse(ROOT / "evidence/knowledge-update/regression-tests.xml").getroot().findall("testsuite")
    test_count = sum(int(s.get("tests", 0)) for s in suites)
    tests_passed = all(int(s.get(key, 0)) == 0 for s in suites for key in ("failures", "errors", "skipped"))
    qa_path = ROOT / "evidence/knowledge-update/native-qa-agents.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
    ai_path = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
    ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
    version_path = ROOT / "evidence/knowledge-update/native-version-fixture.json"
    version_run = json.loads(version_path.read_text(encoding="utf-8")) if version_path.exists() else {}
    consultation_path = ROOT / "evidence/knowledge-update/native-consultation.json"
    consultation = json.loads(consultation_path.read_text(encoding="utf-8")) if consultation_path.exists() else {}
    ocr_path = ROOT / "evidence/knowledge-update/henan-scan-ocr.json"
    ocr = json.loads(ocr_path.read_text(encoding="utf-8")) if ocr_path.exists() else {}
    background_ocr_path = ROOT / "evidence/knowledge-update/background-ocr-check.json"
    background_ocr = json.loads(background_ocr_path.read_text(encoding="utf-8")) if background_ocr_path.exists() else {}
    startup_evidence = {}
    for filename in ("startup-recovery-first-pass.json", "startup-healthy-followup.json", "startup-manual-control.json"):
        path = ROOT / "evidence/knowledge-update" / filename
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        startup_evidence[filename] = {"captured_at": record.get("captured_at"), "passed": record.get("passed", False),
                                      "checks": len(record.get("checks", {}))}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "counts": status["counts"],
              "enabled_source_ids": [s["id"] for s in status["sites"] if s["enabled"]],
              "healthy_monitored_source_ids": [s["id"] for s in status["sites"] if s["enabled"] and not s["paused_reason"] and not s["failures"] and s["last_success"] and not s["stale"]],
              "paused_source_ids": [s["id"] for s in status["sites"] if s["paused_reason"]],
              "test_count": test_count, "tests_passed": tests_passed, "platform_tools": tool_names,
              "public_files_scanned": scanned, "credential_hit_paths": credential_hits,
              "business_review_completed": False, "official_knowledge_published": status["counts"]["heads"],
              "existing_agents_changed": False,
              "new_qa_agents": qa.get("qa_agents", {}), "model_fixed_question_smoke_passed": qa.get("passed", False),
              "model_smoke_cases": len(qa.get("records", [])), "business_answer_accuracy_tested": False,
              "discovery": status["discovery"], "ai_reference_cases": len(ai.get("cases", [])),
              "ai_reference_status": ai.get("status"), "human_attestations_created": 0,
              "synthetic_native_version_test_passed": version_run.get("passed", False),
              "synthetic_native_version_cases": len(version_run.get("records", [])),
              "dynamic_consultation_preview_passed": consultation.get("passed", False),
              "dynamic_consultation_preview_cases": len(consultation.get("records", [])),
              "dynamic_consultation_preview_agents": consultation.get("preview_agents", {}),
              "native_pdf_ocr_passed": ocr.get("passed"), "native_pdf_ocr_sources": ocr.get("normal_guarded_monitored_sources"),
              "native_pdf_ocr_pages": ocr.get("automatic_ocr_pages"), "background_pdf_ocr_passed": background_ocr.get("passed"),
              "local_startup": status["startup"], "startup_evidence": startup_evidence,
              "github_final_push_completed": False,
              "scope": "Actual candidate intake, monitoring, MCP transport/catalog and separately recorded fixed/dynamic-question model stop tests; no business-accuracy or official-policy approval claim."}
    (ROOT / "evidence/knowledge-update/intake-step-status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert tests_passed


if __name__ == "__main__":
    main()

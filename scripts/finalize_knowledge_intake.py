"""Verify the platform catalog and record the actual state of this source-intake step."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import httpx

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_review import development_registry
from nexent_api import NexentAPI


ROOT = Path(__file__).resolve().parents[1]
NAME = "govkb_public_evidence_mcp"


def model_trial_summary(trial, review):
    """Use semantic results only when they belong to this complete frozen run."""
    matched = bool(trial.get("run_id")) and (
        review.get("run_id") == trial["run_id"]
        and bool(trial.get("reference_sha256"))
        and review.get("frozen_reference_sha256") == trial["reference_sha256"]
        and bool(trial.get("rubric_sha256"))
        and review.get("frozen_rubric_sha256") == trial["rubric_sha256"]
        and trial.get("case_count", 0) == trial.get("completed_cases") == review.get("reviewed_cases") == 30
    )
    return {
        "run_id": trial.get("run_id"), "prompt_profile": trial.get("prompt_profile", "raw_v1"),
        "completed_cases": trial.get("completed_cases", 0),
        "technical_pass_count": trial.get("technical_pass_count", 0),
        "review_run_id_observed": review.get("run_id"), "review_matches_complete_frozen_run": matched,
        "semantic_review_status": "ai_review_complete" if matched else "pending_matching_complete_review",
        **{key: review.get(key) if matched else None for key in (
            "ai_semantic_pass_count", "ai_semantic_fail_count", "not_assessable_count",
            "both_program_and_ai_review_pass_count")},
        "business_release_ready": False, "independent_human_acceptance": False,
        "retrieval_recall_measured": False,
    }


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    with store.db() as db:
        for owner, sources in (("official-intake", ("MPS-NORM-01", "MPS-USE-01")), ("policy-discovery", ("BEIJING-POLICY-DISCOVERY",))):
            incomplete = db.execute("SELECT 1 FROM jobs WHERE site_id IN ("+",".join("?" for _ in sources)+") AND state!='done' LIMIT 1", sources).fetchone()
            if not incomplete:
                db.execute("UPDATE workers SET progress='completed' WHERE id=?", (owner,))
    api = None
    tool_names = []
    platform_failure = None
    platform_stage = "client_initialization"
    try:
        api = NexentAPI()
        platform_stage = "account_loading"
        accounts = json.loads((ROOT / ".runtime/m2/accounts.json").read_text(encoding="utf-8"))
        platform_stage = "login"
        api.login(accounts["developer"])
        platform_stage = "service_catalog"
        service = next(s for s in api.call("GET", "/mcp/list")["remote_mcp_server_list"]
                       if s.get("remote_mcp_server_name", s.get("mcp_name")) == NAME)
        service_id = next((service[key] for key in ("mcp_id", "remote_mcp_server_id", "id") if key in service), None)
        if service_id is None:
            raise RuntimeError("MCP identifier not present; available field names: "+", ".join(service))
        platform_stage = "tool_catalog"
        tools = api.call("GET", "/mcp/tools?mcp_id="+str(service_id))["tools"]
        tool_names = sorted(tool.get("name", tool.get("tool_name")) for tool in tools)
        assert tool_names == ["govkb_get_environment_status", "govkb_search_evidence"]
    except (httpx.HTTPError, OSError, ValueError, KeyError, StopIteration, AssertionError, RuntimeError) as error:
        # Persist a current failed check without leaking response bodies or credentials.
        platform_failure = type(error).__name__
    finally:
        if api is not None:
            api.close()
    if platform_failure is None:
        mcp_path = ROOT / "evidence/knowledge-update/mcp-connection-2026-10-15.json"
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
        mcp.update(platform_catalog_verified=True, platform_tool_names=tool_names)
        mcp_path.write_text(json.dumps(mcp, ensure_ascii=False, indent=2), encoding="utf-8")
    status = store.status()
    policy = publication_policy(store)
    with store.db() as db:
        development_questions = development_registry(db)
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
    attachment_path = ROOT / "evidence/knowledge-update/official-attachment-flow.json"
    attachment_proof = json.loads(attachment_path.read_text(encoding="utf-8")) if attachment_path.exists() else {}
    model_path = ROOT / "evidence/knowledge-update/model-prevalidation.json"
    model_trial = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else {}
    model_plan_path = ROOT / "evidence/knowledge-update/model-prevalidation-plan.json"
    model_plan = json.loads(model_plan_path.read_text(encoding="utf-8")) if model_plan_path.exists() else {}
    ai_review_path = ROOT / "evidence/knowledge-update/model-prevalidation-ai-review.json"
    model_review = json.loads(ai_review_path.read_text(encoding="utf-8")) if ai_review_path.exists() else {}
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
              "platform_catalog_verified": platform_failure is None,
              "platform_catalog_failure_class": platform_failure,
              "platform_catalog_failure_stage": platform_stage if platform_failure else None,
              "public_files_scanned": scanned, "credential_hit_paths": credential_hits,
              "business_review_completed": False, "official_knowledge_published": status["counts"]["heads"],
              "publication_policy": policy,
              "evaluation_review_contract": policy.get("review_contract", "legacy_human_v1"),
              "registered_development_questions": len(development_questions),
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
              "attachment_discovery": status["attachments"],
              "waiting_attachment_collectors": [s["id"] for s in status["sites"] if s.get("waiting_for_attachment")],
              "isolated_actual_attachment_flow_passed": attachment_proof.get("passed"),
              "given_evidence_model_prevalidation": model_trial_summary(model_trial, model_review),
              "pending_model_prevalidation_plan": {key: model_plan.get(key) for key in (
                  "run_id", "kind", "case_count", "retrieval_run_id", "model_server_checked_at_preparation")}
                  if model_plan.get("run_id") != model_trial.get("run_id") else None,
              "local_startup": status["startup"], "startup_evidence": startup_evidence,
              "github_final_push_completed": False,
              "scope": "Actual candidate intake, monitoring, MCP transport/catalog and separately recorded fixed/dynamic-question model stop tests; no business-accuracy or official-policy approval claim."}
    (ROOT / "evidence/knowledge-update/intake-step-status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert tests_passed and platform_failure is None, "Current regression and platform catalog checks must both pass"


if __name__ == "__main__":
    main()

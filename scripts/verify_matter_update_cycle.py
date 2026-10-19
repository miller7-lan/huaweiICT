"""One real four-source observation and registration/evolution trial, no model calls."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle
from gov_mcp.matter_native import FACTS
from gov_mcp.matter_update import MatterUpdater, build_snapshot, inspect_capture, encoded
from capture_nanjing_rule_sources import collect, SOURCES, NATIONAL_LAW
from prepare_matter_update import LOCAL, ACTOR, TEMPLATE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8786)
    observation = parser.add_mutually_exclusive_group(required=True)
    observation.add_argument("--collect", action="store_true")
    observation.add_argument("--capture-root", type=Path, help="使用已实际采集且未过期的新观察；本命令不再次发送官网请求")
    args = parser.parse_args()
    marker = args.runtime / "cycle-started.json"
    if marker.exists(): parser.error("Existing update observation retained; no automatic repeat requests")
    marker.write_bytes(encoded({"started_at": datetime.now(timezone.utc).isoformat(), "model_calls": 0}))
    prep = json.loads((args.runtime / "update-preparation.json").read_text(encoding="utf-8"))
    snapshots = Path(prep["snapshot_root"])
    service = MatterLifecycle(args.runtime / "service")
    updater = MatterUpdater(args.runtime, service)
    accounts = json.loads((ROOT / ".runtime/accounts.json").read_text(encoding="utf-8-sig"))
    checks, clients = [], {}
    report = {"schema": 1, "started_at": datetime.now(timezone.utc).isoformat(), "status": "RUNNING",
        "scope": "actual public originals in controlled source registration; not a historical website policy change",
        "checks": checks, "model_calls": 0, "native_agent_calls": 0, "jev_calls": 0,
        "human_business_review": False, "formal_publication": False, "independent_evaluation": False}
    injected = False; final_version = None; owned_rollback = None
    pause_tag = "Controlled update-trial dependency pause:" + report["started_at"]

    def check(name, value):
        checks.append({"check": name, "passed": bool(value)})
        if not value: raise AssertionError(name)

    def post(role, operation, payload): return clients[role].post("/api/" + operation, json=payload)

    try:
        base = f"http://127.0.0.1:{args.port}"
        for role in ("citizen", "staff"):
            client = httpx.Client(base_url=base, trust_env=False, timeout=15, headers={"Origin": base})
            clients[role] = client
            r = client.post("/api/login", json={"username": accounts[role]["email"], "password": accounts[role]["password"]})
            check(role + "_real_nexent_login", r.status_code == 200)
            identity = client.get("/api/session").json()
            check(role + "_trusted_identity", identity.get("role") == role and identity.get("authentication_mode") == "nexent_accounts")
            client.headers["X-CSRF-Token"] = identity["csrf"]
        old = post("citizen", "query", {"facts": FACTS, "topic": "old_card_lifecycle"}).json()
        check("three_source_baseline_stops_complete_proof", old.get("status") == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF" and old.get("version") == prep["baseline_version"])
        report["old_case_id"] = old["case_id"]
        selected = [s for s in SOURCES + NATIONAL_LAW if s[3] == "html" and s[0] in LOCAL + ["LAW-ID-BJ-TRAFFIC-2011"]]
        capture = args.capture_root or (args.runtime / "fresh-capture")
        observed = collect(sources=tuple(selected), runtime=capture) if args.collect else json.loads((capture / "snapshot.json").read_text(encoding="utf-8"))
        report["official_http_sent_by_this_command"] = bool(args.collect)
        report["actual_http_receipts"] = [{"source_id": r["source_id"], "status": r["status"],
            "checked_at": r.get("checked_at"), "requests": r["requests"]} for r in observed["sources"]]
        inspection = inspect_capture(TEMPLATE, capture, [s[0] for s in selected]); report["template_comparison"] = inspection
        problems = [r["source_id"] for r in inspection if r["kind"] not in ("unchanged", "raw_only_changed")]
        if problems:
            report["blocked_observation"] = updater.block_capture(capture, problems,
                reason="Actual source access/body/metadata differs; re-review required, no automatic resume", principal=ACTOR)
            raise RuntimeError("SOURCE_OBSERVATION_REQUIRES_REVIEW")
        check("all_four_actual_official_originals_observed", len(inspection) == 4)
        before_versions = len(service.store.rows("versions")); before_candidates = len(service.store.rows("candidates"))
        three = snapshots / "observed-three"; build_snapshot(TEMPLATE, three, LOCAL, capture_root=capture)
        unchanged = updater.stage(three, ontology_stage="seed", expected_head=service.head(), principal=ACTOR)
        same = updater.release_engineering(unchanged["update_id"], note="Actual HTTP recheck of three registered sources; whole bodies and metadata unchanged", principal=ACTOR)
        check("actual_no_change_keeps_version_and_candidate_counts", same["version"] == old["version"]
            and len(service.store.rows("versions")) == before_versions and len(service.store.rows("candidates")) == before_candidates
            and unchanged["status"] == "NO_SEMANTIC_CHANGE")
        repeated = updater.stage(three, ontology_stage="seed", expected_head=service.head(), principal=ACTOR)
        check("same_observation_replay_no_extra_processing", repeated["replayed"] and repeated["update_id"] == unchanged["update_id"])
        four = snapshots / "registered-four"; build_snapshot(TEMPLATE, four, LOCAL + ["LAW-ID-BJ-TRAFFIC-2011"], capture_root=capture)
        source_added = updater.stage(four, ontology_stage="seed", expected_head=service.head(), principal=ACTOR)
        source_version = updater.release_engineering(source_added["update_id"], note="Register one actually fetched national-law HTML source; retain seed ontology as source-only control", principal=ACTOR)["version"]
        source_query = post("citizen", "query", {"facts": FACTS, "topic": "old_card_lifecycle"}).json()
        check("new_source_without_relation_evolution_still_stops", source_query.get("status") == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF" and source_query.get("version") == source_version)
        evolved = updater.stage(four, ontology_stage="evolved", expected_head=service.head(), principal=ACTOR)
        final_version = updater.release_engineering(evolved["update_id"], note="Release typed return_at relation and composition in engineering preview; human review pending", principal=ACTOR)["version"]
        new = post("citizen", "query", {"facts": FACTS, "topic": "old_card_lifecycle"}).json()
        check("source_and_ontology_update_changes_live_web_query", new.get("status") == "DRAFT_PROOF_READY" and new.get("version") == final_version
            and new.get("proofs") and all(len(p["source_ids"]) >= 2 for p in new["proofs"]))
        previous = post("citizen", "read", {"case_id": old["case_id"]}).json()
        check("old_web_case_remains_old_version_and_incomplete", previous.get("version") == old["version"] and previous.get("status") == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF")
        handoff = post("citizen", "handoff", {"case_id": new["case_id"]}); check("new_version_handoff_created", handoff.status_code == 200)
        accepted = post("staff", "accept", {"case_id": new["case_id"], "token": handoff.json()["handoff_token"]})
        check("staff_receives_updated_version_conditions_and_proofs", accepted.status_code == 200 and accepted.json().get("version") == final_version
            and accepted.json().get("declared_conditions") == FACTS and accepted.json().get("status") == "DRAFT_PROOF_READY")
        confirmed = post("staff", "confirm", {"case_id": new["case_id"], "acknowledge": True,
            "note": "Automated real source-registration update engineering handoff; not human business review"})
        check("updated_handoff_confirmation_is_not_business_approval", confirmed.status_code == 200 and confirmed.json().get("formal_business_approval") is False)
        service.set_source_enabled("LAW-ID-BJ-TRAFFIC-2011", False, reason=pause_tag, principal=ACTOR); injected = True
        for role in ("citizen", "staff"):
            stopped = post(role, "read", {"case_id": new["case_id"]})
            check(role + "_new_history_stops_on_current_law_pause", stopped.status_code == 409 and stopped.json().get("code") == "SOURCE_PAUSED")
        service.rollback(old["version"], final_version, principal=ACTOR)
        owned_rollback = old["version"]
        stopped = post("staff", "read", {"case_id": new["case_id"]})
        check("rollback_does_not_bypass_new_case_dependency_pause", stopped.status_code == 409 and stopped.json().get("code") == "SOURCE_PAUSED")
        legacy = post("citizen", "read", {"case_id": old["case_id"]})
        check("old_incomplete_case_does_not_gain_the_paused_relation", legacy.status_code == 200 and legacy.json().get("status") == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF")
        if service.store.get("sources", "LAW-ID-BJ-TRAFFIC-2011")["reason"] != pause_tag:
            raise RuntimeError("INJECTED_PAUSE_OWNERSHIP_CHANGED")
        service.set_source_enabled("LAW-ID-BJ-TRAFFIC-2011", True, reason="Restore only this trial's injected pause", principal=ACTOR); injected = False
        service.rollback(final_version, owned_rollback, principal=ACTOR); owned_rollback = None
        final = post("staff", "read", {"case_id": new["case_id"]})
        check("restored_engineering_head_preserves_confirmed_case", final.status_code == 200 and final.json().get("version") == final_version and final.json().get("state") == "staff_confirmed")
        report.update(status="PASSED", versions={"baseline_three_seed": old["version"], "four_seed": source_version, "four_evolved": final_version},
            updates={"unchanged": unchanged["update_id"], "source_added": source_added["update_id"], "ontology_evolved": evolved["update_id"]},
            added_rules=source_added["delta"]["added_rules"], source_delta=source_added["delta"]["sources"], new_case_id=new["case_id"],
            proof_count=len(new["proofs"]), human_review=new["human_review"])
    except Exception as error:
        report.update(status="PARTIAL_BLOCKED", error_type=type(error).__name__)
        if isinstance(error, RuntimeError) and str(error) == "SOURCE_OBSERVATION_REQUIRES_REVIEW": report["error_code"] = str(error)
    finally:
        if injected:
            try:
                control = service.store.get("sources", "LAW-ID-BJ-TRAFFIC-2011")
                if control and control.get("reason") == pause_tag:
                    service.set_source_enabled("LAW-ID-BJ-TRAFFIC-2011", True, reason="Restore only owned trial injection after interrupted verification", principal=ACTOR)
                else: raise RuntimeError("INJECTED_PAUSE_OWNERSHIP_CHANGED")
            except Exception as cleanup:
                report.update(status="PARTIAL_BLOCKED", cleanup_error_type=type(cleanup).__name__)
        if final_version and owned_rollback:
            try:
                service.rollback(final_version, owned_rollback, principal=ACTOR)
            except Exception as cleanup:
                report.update(status="PARTIAL_BLOCKED", rollback_cleanup_error_type=type(cleanup).__name__)
        for client in clients.values():
            try: client.post("/api/logout", json={})
            except httpx.HTTPError: pass
            client.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (args.runtime / "cycle-report.json").write_bytes(encoded(report))
        target = ROOT / "evidence/real-matter-closure/update-cycle/stage-04-update-live.json"
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(encoded(report))
    print(json.dumps({"status": report["status"], "checks": len(checks), "passed": sum(c["passed"] for c in checks),
        "model_calls": 0, "error_type": report.get("error_type"), "error_code": report.get("error_code")}, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__": raise SystemExit(main())

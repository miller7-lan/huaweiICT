"""Execute an isolated persisted engineering lifecycle on actual public sources.

The two app roles are program-controlled; no human or business approval is claimed.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle, LifecycleError, Principal

PACK = ROOT / "data/real-matter-closure/nanjing/review-pack-v1"
RUNTIME = ROOT / ".runtime/real-matter-closure/lifecycle-stage03"
OUTPUT = ROOT / "evidence/real-matter-closure/stage-03-persisted-lifecycle.json"
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
         "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu",
         "old_card_valid": True}


def run():
    if RUNTIME.exists():
        raise ValueError("既有生命周期活动保留；默认读取证据，不重置或重复运行")
    app = MatterLifecycle(RUNTIME)
    operator = Principal("engineering-operator", "maintainer", "service")
    citizen = Principal("fictional-citizen-session", "citizen", "service")
    staff = Principal("engineering-staff-session", "staff", "service")
    first = app.prepare(PACK, ontology_stage="seed", principal=operator)
    v1 = app.release_preview(first["candidate_id"], None,
        note="Source-bound engineering seed release; no human approval", principal=operator)["version"]
    old_case = app.open_case(FACTS, principal=citizen)
    second = app.prepare(PACK, ontology_stage="evolved", principal=operator)
    v2 = app.release_preview(second["candidate_id"], v1,
        note="Typed relation evolution preview; no human approval", principal=operator)["version"]
    current = app.open_case(FACTS, principal=citizen)
    handoff = app.handoff(current["case_id"], principal=citizen)
    received = app.accept(current["case_id"], handoff["handoff_token"], principal=staff)
    confirmed = app.confirm_handoff(current["case_id"], acknowledge=True,
        note="Automated engineering handoff confirmation only", principal=staff)
    old_read = app.read_case(old_case["case_id"], principal=citizen)
    app.set_source_enabled("NJ-OLD-CARD-2025", False,
        reason="Controlled pause injection; not an observed official-source withdrawal", principal=operator)
    blocked = []
    for label, version in (("current", v2), ("historical", v1)):
        try:
            app.query(FACTS, principal=citizen, version_id=version)
        except LifecycleError as error:
            if not str(error).startswith("SOURCE_PAUSED:"):
                raise
            blocked.append(label)
    app.rollback(v1, v2, principal=operator)
    rollback_paused = False
    try:
        app.query(FACTS, principal=citizen)
    except LifecycleError as error:
        rollback_paused = str(error).startswith("SOURCE_PAUSED:")
    app.set_source_enabled("NJ-OLD-CARD-2025", True,
        reason="Explicit restoration after the controlled pause check", principal=operator)
    app.rollback(v2, v1, principal=operator)
    restarted = MatterLifecycle(RUNTIME)
    final = restarted.query(FACTS, principal=citizen)
    with app.store.db() as db:
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("candidates", "versions", "runs", "audit")}
    checks = {
        "seed_without_complete_proof": old_case["status"] == "NO_SUPPORTED_MULTI_DOCUMENT_PROOF",
        "evolved_preview_has_multi_document_proof": current["status"] == "DRAFT_PROOF_READY",
        "old_case_pinned_to_v1": old_read["version"] == v1 and old_read["status"] == old_case["status"],
        "handoff_preserves_v2": received["version"] == v2 and confirmed["version"] == v2,
        "staff_explicit_engineering_confirmation": confirmed["state"] == "staff_confirmed",
        "pause_blocks_current_and_historical": blocked == ["current", "historical"],
        "rollback_does_not_bypass_current_pause": rollback_paused,
        "restart_keeps_head_and_objects": restarted.head() == v2 and final["status"] == "DRAFT_PROOF_READY",
        "two_immutable_versions": counts["versions"] == 2,
        "no_business_approval": not confirmed["formal_business_approval"] and not final["knowledge_published"],
    }
    if not all(checks.values()):
        raise ValueError("Persisted lifecycle verification failed")
    report = {"stage": "03_persisted_real_source_engineering_lifecycle", "passed": True,
        "scope": "Actual local persistence and role boundary execution on public source snapshots; automated roles, no browser/native model or human business acceptance",
        "checks": checks, "versions": {"seed": v1, "evolved": v2}, "counts": counts,
        "model_api_calls": 0, "http_ui_calls": 0, "human_business_review": False,
        "formal_business_publication": False, "controlled_pause_injection": True,
        "old_business_databases_opened": False, "windows_recovery_tasks_changed": False,
        "handoff_tokens_in_report": False}
    OUTPUT.write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="首次执行独立生命周期活动；拒绝覆盖")
    args = parser.parse_args()
    value = run() if args.run else json.loads(OUTPUT.read_text(encoding="utf-8"))
    print(json.dumps(value, ensure_ascii=False, indent=2))

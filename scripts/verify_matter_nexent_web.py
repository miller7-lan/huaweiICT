"""One real loopback web/auth handoff check; never starts an Agent or model."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from private_runtime import ensure_private_directory

FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
    "self": True, "civil_capacity": "full", "household": "jiangsu", "reason": "expiry",
    "old_card_valid": None, "urgent_exam": False, "fee_relief_requested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8784)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, default=ROOT / ".runtime/accounts.json")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid local port")
    settings = json.loads((args.runtime / "web-settings.json").read_text())
    if settings.get("auth_mode") != "nexent_accounts":
        parser.error("Real Nexent authentication required; no engineering fallback")
    private = args.runtime / "identity-acceptance"
    ensure_private_directory(private)
    receipt_path = private / "receipt.json"
    marker = private / "started.json"
    if marker.exists() or receipt_path.exists():
        parser.error("Existing run is preserved; do not overwrite or repeat this activity")
    marker.write_bytes(b'{"purpose":"real_nexent_web_identity_handoff"}\n')
    accounts = json.loads(args.accounts.read_text(encoding="utf-8-sig"))
    base = f"http://127.0.0.1:{args.port}"
    checks = []
    receipt = {"schema": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "execution": "actual_loopback_http_with_existing_nexent_accounts",
        "automated_operator": True, "checks": checks, "status": "RUNNING",
        "new_model_calls": 0, "native_agent_execution": False,
        "human_business_review": False, "formal_business_publication": False,
        "independent_evaluation": False}
    clients = {}; injected = False

    def check(label, condition):
        checks.append({"check": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    try:
        for account_role, expected in (("citizen", "citizen"), ("staff", "staff"), ("developer", "maintainer")):
            client = httpx.Client(base_url=base, trust_env=False, timeout=15,
                headers={"Origin": base})
            clients[expected] = client
            login = client.post("/api/login", json={"username": accounts[account_role]["email"],
                "password": accounts[account_role]["password"]})
            check(expected + "_real_signin", login.status_code == 200)
            session = client.get("/api/session").json()
            check(expected + "_trusted_identity", session.get("role") == expected
                and session.get("authentication_mode") == "nexent_accounts" and session.get("logged_in"))
            client.headers["X-CSRF-Token"] = session["csrf"]
        citizen, staff, maint = (clients[r] for r in ("citizen", "staff", "maintainer"))
        check("client_role_override_rejected", citizen.post("/api/query",
            json={"facts": FACTS, "topic": "materials", "role": "maintainer"}).status_code == 400)
        source_state = maint.get("/api/state").json()
        source_id = "NJ-POLICE-FAQ-2025"
        check("injection_source_initially_enabled", any(s["source_id"] == source_id and s["enabled"] for s in source_state["sources"]))
        response = citizen.post("/api/query", json={"facts": FACTS, "topic": "materials"})
        check("source_bound_query", response.status_code == 200 and response.json().get("status") == "SOURCE_GUIDANCE_PENDING_REVIEW")
        case = response.json(); case_id = case["case_id"]
        receipt["case_id"], receipt["version"] = case_id, case["version"]
        check("staff_before_handoff_denied", staff.post("/api/read", json={"case_id": case_id}).status_code == 403)
        check("citizen_cannot_pause_source", citizen.post("/api/source",
            json={"source_id": source_id, "enabled": False, "reason": "unauthorized check"}).status_code == 403)
        handoff = citizen.post("/api/handoff", json={"case_id": case_id})
        check("handoff_created", handoff.status_code == 200)
        payload = {"case_id": case_id, "token": handoff.json()["handoff_token"]}
        accepted = staff.post("/api/accept", json=payload)
        check("handoff_same_conditions_and_version", accepted.status_code == 200
            and accepted.json()["version"] == case["version"] and accepted.json()["declared_conditions"] == FACTS)
        check("handoff_token_single_use", staff.post("/api/accept", json=payload).status_code == 409)
        done = staff.post("/api/confirm", json={"case_id": case_id, "acknowledge": True,
            "note": "Automated real Nexent identity integration; candidate only, no human review"})
        check("engineering_confirmation_only", done.status_code == 200
            and done.json().get("state") == "staff_confirmed" and done.json().get("formal_business_approval") is False)
        pause = maint.post("/api/source", json={"source_id": source_id, "enabled": False,
            "reason": "Controlled real Nexent identity integration pause"})
        injected = pause.status_code == 200
        check("maintainer_pause", injected)
        for role in ("citizen", "staff"):
            blocked = clients[role].post("/api/read", json={"case_id": case_id})
            check(role + "_history_stopped", blocked.status_code == 409 and blocked.json().get("code") == "SOURCE_PAUSED")
        receipt["status"] = "PASSED"
    except Exception as error:
        receipt["status"] = "FAILED"
        receipt["error_type"] = type(error).__name__
    finally:
        if injected:
            try:
                restored = clients["maintainer"].post("/api/source", json={"source_id": "NJ-POLICE-FAQ-2025",
                    "enabled": True, "reason": "Restore only this activity's controlled source pause"})
                restored_ok = restored.status_code == 200
            except httpx.HTTPError:
                restored_ok = False
            checks.append({"check": "injected_pause_restored", "passed": restored_ok})
            if not restored_ok: receipt["status"] = "FAILED"
        for client in clients.values():
            try: client.post("/api/logout", json={})
            except httpx.HTTPError: pass
            client.close()
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        receipt_path.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"status": receipt["status"], "checks": len(checks),
        "passed": sum(c["passed"] for c in checks), "new_model_calls": 0,
        "credentials_printed": False, "receipt": str(receipt_path)}, ensure_ascii=False))
    return 0 if receipt["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

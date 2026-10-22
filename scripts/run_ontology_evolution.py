"""Offline ontology workspace; real source structure stays pending until confirmed."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.ontology_evolution import OntologyWorkspace, PublicOntologySource
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/ontology-evolution-20261019"
OUTPUT = ROOT / "evidence/ontology-evolution-20261019"
FACTS = {"local_household": True, "age_years": 30, "card_years": 2,
         "photo_years": 3, "fingerprint_count": 1}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def open_workspace():
    # Only this new workspace is writable. Original sources are read-only.
    if not RUNTIME.exists():
        ensure_private_directory(RUNTIME)
    seed = json.loads((ROOT / "config/preparation/ontology.seed.json").read_text("utf-8"))
    source = PublicOntologySource(ROOT / ".runtime/new-knowledge-experiment-20261018")
    return OntologyWorkspace.initialize(RUNTIME, seed, source)


def prepare(workspace):
    candidate_id = workspace.submit()
    status = workspace.status()
    record = next(row for row in status["candidates"] if row["id"] == candidate_id)
    snapshot = {"prepared_at": datetime.now(timezone.utc).isoformat(), "mode": "engineering_structure_review",
                "candidate_id": candidate_id, "expected_head": status["head"], "candidate": record["proposal"],
                "source_binding": record["source_binding"], "candidate_status": record["status"],
                "new_model_calls": 0, "new_http_calls": 0, "business_approval": False,
                "before_query": workspace.query("北京市", "resident-id-renewal", "damaged", "online", FACTS)}
    path = OUTPUT / "candidate.json"
    if path.exists():
        old = json.loads(path.read_text("utf-8"))
        if old["candidate_id"] != candidate_id:
            raise ValueError("SOURCE_OR_CANDIDATE_CHANGED_USE_NEW_REVIEW_ARTIFACT")
    else:
        write(path, snapshot)
    state = "PENDING_HUMAN_STRUCTURAL_CONFIRMATION" if record["status"] == "pending_review" else "STRUCTURE_ALREADY_CONFIRMED_CHECK_CURRENT_VERSION"
    return {"status": state, "candidate_id": candidate_id,
            "expected_head": status["head"], "artifact": str(path), "new_cloud_calls": 0}


def demonstrate(workspace):
    cases = [
        ("all_source_conditions", "北京市", "damaged", FACTS),
        ("missing_fingerprint", "北京市", "damaged", {k: v for k, v in FACTS.items() if k != "fingerprint_count"}),
        ("source_condition_false", "北京市", "damaged", {**FACTS, "age_years": 15}),
        ("unreviewed_boundary", "北京市", "damaged", {**FACTS, "age_years": 16}),
        ("expiry_not_damaged", "北京市", "expiry", FACTS),
        ("other_region", "南京市", "damaged", FACTS),
    ]
    rows = [{"id": name, "input_is_fictional": True,
             "result": workspace.query(region, "resident-id-renewal", branch, "online", deepcopy(facts))}
            for name, region, branch, facts in cases]
    if rows[0]["result"]["status"] != "SOURCE_CONDITIONS":
        raise ValueError("STRUCTURAL_CONFIRMATION_REQUIRED_BEFORE_DEMONSTRATION")
    expected = [("SOURCE_CONDITIONS", "true"), ("SOURCE_CONDITIONS", "unknown"),
                ("SOURCE_CONDITIONS", "false"), ("SOURCE_CONDITIONS", "unknown"),
                ("OUT_OF_SCOPE", "unknown"), ("OUT_OF_SCOPE", "unknown")]
    checks = [tuple(row["result"][k] for k in ("status", "source_conditions_result")) == wanted
              for row, wanted in zip(rows, expected)]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"mode": "reviewed_structure_not_business_approval", "cases": rows, "checks": checks,
              "passed": all(checks), "new_model_calls": 0, "formally_published": False}
    write(OUTPUT / ("demonstration-" + stamp + ".json"), report)
    return {"status": "PASS_SOURCE_CONDITION_DEMONSTRATION" if all(checks) else "FAILED", "cases_passed": sum(checks), "cases_total": len(checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "status", "confirm", "demonstrate", "rollback"), nargs="?", default="prepare")
    parser.add_argument("--candidate")
    parser.add_argument("--expected-head")
    parser.add_argument("--reviewer")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--target")
    args = parser.parse_args()
    workspace = open_workspace()
    if args.action == "prepare": output = prepare(workspace)
    elif args.action == "status": output = workspace.status()
    elif args.action == "confirm":
        if not args.candidate or not args.expected_head or not args.reviewer or not args.confirmed:
            raise ValueError("EXPLICIT_CANDIDATE_HEAD_AND_HUMAN_REVIEWER_REQUIRED")
        version = workspace.review(args.candidate, args.expected_head, args.candidate,
                                   {"kind": "human", "name": args.reviewer}, confirmed=True)
        output = {"status": "STRUCTURE_ACTIVATED_NOT_BUSINESS_APPROVAL", "version": version}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(OUTPUT / ("structural-confirmation-" + stamp + ".json"), output)
    elif args.action == "demonstrate": output = demonstrate(workspace)
    else:
        if not args.target or not args.expected_head or not args.reviewer or not args.confirmed:
            raise ValueError("EXPLICIT_ROLLBACK_AND_HUMAN_REVIEWER_REQUIRED")
        version = workspace.rollback(args.target, args.expected_head, {"kind": "human", "name": args.reviewer}, confirmed=True)
        output = {"status": "ROLLED_BACK", "version": version}
    print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

"""Prepare a Henan archive candidate; real activation needs specific confirmation.

No network, model, OCR, Agent deployment or formal business publication. Earlier
source originals and observations are read without changing or re-approving them.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.henan_multimodal import FACTS, HenanMultimodalWorkspace, integration_seed
from gov_mcp.henan_multimodal_sources import ArchivedHenanMultimodalSource, SPECS
from gov_mcp.json_output import object_from_json
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/henan-multimodal-20261022"
PREPARATION = ROOT / ".runtime/multimodal-preparation-20261021"
EVIDENCE = ROOT / "evidence/competition-multimodal-20261021"
OUTPUT = ROOT / "evidence/henan-multimodal-20261022"


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def preserved_inputs():
    paths = [ROOT / "config/decision/connections.json", *(EVIDENCE / name for name in
             ("preparation.json", "local-extraction.json", "scan-visual-observation.json"))]
    paths += [PREPARATION / (spec["source_id"] + "." + spec["kind"]) for spec in SPECS.values()]
    paths += [PREPARATION / (spec["source_id"] + "-attempt.json") for spec in SPECS.values()]
    paths += [PREPARATION / ("scan-" + str(page) + ".png") for page in (5, 6)]
    paths.append(PREPARATION / "acquisition/knowledge.sqlite3")
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def open_workspace(*, create=False):
    if create:
        ensure_private_directory(RUNTIME)
        gate_path = RUNTIME / "gates.json"
        if not gate_path.exists():
            write_new(gate_path, {"schema_version": 1, "purpose": "fixed_archive_explanation_only",
                "business_conclusions_paused": True,
                "sources": {spec["source_id"]: {"enabled": True, "paused_reason": None,
                            "failed": False, "superseded": False} for spec in SPECS.values()}})
    elif not (RUNTIME / "ontology/ontology-workspace.json").is_file():
        raise ValueError("PREPARE_NEW_ISOLATED_WORKSPACE_FIRST")
    source = ArchivedHenanMultimodalSource(PREPARATION, EVIDENCE / "scan-visual-observation.json",
                                          EVIDENCE / "local-extraction.json", RUNTIME / "gates.json")
    source()  # A missing, changed or paused original never creates ontology state.
    if create:
        seed = integration_seed(object_from_json((ROOT / "config/preparation/ontology.seed.json").read_bytes()))
        return HenanMultimodalWorkspace.initialize(RUNTIME / "ontology", seed, source)
    return HenanMultimodalWorkspace(RUNTIME / "ontology", source)


def prepare():
    before = preserved_inputs()
    workspace = open_workspace(create=True)
    identifier = workspace.submit()
    record = workspace.store.get("candidates", identifier)
    result = workspace.query(FACTS)
    if (record["status"] != "pending_review" or result["status"] != "UNSUPPORTED_RELATION"
            or workspace._version(workspace.status()["head"])["data"]["instances"]):
        raise ValueError("PREPARATION_MUST_REMAIN_UNACTIVATED")
    if preserved_inputs() != before:
        raise ValueError("PREVIOUS_INPUT_CHANGED_DURING_PREPARATION")
    artifact = {"prepared_at": datetime.now(timezone.utc).isoformat(), "status": "READY_FOR_SPECIFIC_STRUCTURE_REVIEW",
                "candidate_id": identifier, "expected_head": workspace.status()["head"],
                "source_binding": record["source_binding"], "implementation": workspace._implementation(),
                "candidate": record["proposal"], "before_query": result, "protected_inputs": before,
                "new_http_calls": 0, "model_calls": 0, "native_runs": 0, "ontology_activated": False,
                "business_approval": False, "formally_published": False,
                "scope": "Actual three archived originals and attributed AI scan transcription, candidate preparation only."}
    path = OUTPUT / "candidate.json"
    if path.exists():
        previous = object_from_json(path.read_bytes())
        if previous["candidate_id"] != identifier or previous["expected_head"] != artifact["expected_head"]:
            raise ValueError("NEW_CANDIDATE_REQUIRES_NEW_REVIEW_ARTIFACT")
    else:
        write_new(path, artifact)
    return {key: artifact[key] for key in ("status", "candidate_id", "expected_head", "source_binding", "implementation",
                                          "new_http_calls", "model_calls", "native_runs", "ontology_activated")}


def demonstrate(candidate, baseline, reviewer):
    workspace = open_workspace()
    artifact = object_from_json((OUTPUT / "candidate.json").read_bytes())
    if (candidate, baseline) != (artifact["candidate_id"], artifact["expected_head"]):
        raise ValueError("EXACT_NEW_CANDIDATE_AND_BASELINE_CONFIRMATION_REQUIRED")
    before = preserved_inputs()
    actor = {"kind": "human", "name": reviewer}
    active = workspace.review(candidate, baseline, candidate, actor, confirmed=True)
    cases = []
    try:
        cases.append({"id": "three_modality_archive_explanation", "result": workspace.query(FACTS, expected_version=active)})
        for name, changed in (("current_business_stops", {"purpose": "current_business"}),
                              ("other_region_stops", {"region": "南京市"}),
                              ("other_branch_stops", {"branch": "first_issue"}),
                              ("private_field_stops", {"applicant_id": "fictional-never-persist"})):
            cases.append({"id": name, "result": workspace.query(dict(FACTS, **changed))})
    finally:
        workspace.rollback(baseline, active, actor, confirmed=True)
    after = workspace.query(FACTS)
    checks = {"archive_plan_executed": cases[0]["result"]["status"] == "derived_archive",
              "four_dependent_nodes": len(cases[0]["result"]["trace"]) == 4,
              "current_and_other_scopes_stop": all(row["result"]["status"] in ("unknown", "out_of_scope") for row in cases[1:5]),
              "rollback_disables_relation": after["status"] == "UNSUPPORTED_RELATION",
              "old_originals_and_flags_unchanged": preserved_inputs() == before}
    report = {"status": "PASS_LOCAL_HENAN_ARCHIVE_DEMO" if all(checks.values()) else "FAILED",
              "candidate_id": candidate, "baseline": baseline, "activated_version": active,
              "current_head": workspace.status()["head"], "checks": checks, "cases": cases,
              "after": after, "protected_inputs": before, "model_calls": 0, "http_calls": 0, "native_runs": 0,
              "formal_approval": False, "formally_published": False,
              "scope": "Reviewed isolated archive explanation, not current policy or independent business acceptance."}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write_new(OUTPUT / ("demonstration-" + stamp + ".json"), report)
    return {"status": report["status"], "checks": checks, "rolled_back": report["current_head"] == baseline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="prepare", choices=("prepare", "status", "confirm-demo"))
    parser.add_argument("--candidate")
    parser.add_argument("--expected-head")
    parser.add_argument("--reviewer")
    parser.add_argument("--confirmed", action="store_true")
    args = parser.parse_args()
    if args.action == "confirm-demo" and not (args.confirmed and args.candidate and args.expected_head and args.reviewer):
        raise ValueError("SPECIFIC_HUMAN_STRUCTURE_AND_ROLLBACK_CONFIRMATION_REQUIRED")
    if args.action == "prepare":
        result = prepare()
    elif args.action == "status":
        result = open_workspace().status()
    else:
        result = demonstrate(args.candidate, args.expected_head, args.reviewer)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

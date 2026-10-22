"""Local archive -> reviewed ontology -> typed cross-document execution demo.

Default prepare never activates a candidate. No action invokes a model or HTTP.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS, load_bundle
from gov_mcp.ontology_crossdoc import ArchivedCrossdocSource, CrossdocOntologyWorkspace, integration_seed
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/ontology-crossdoc-20261020"
OUTPUT = ROOT / "evidence/ontology-crossdoc-20261020"
ORIGINAL = ROOT / ".runtime/crossdoc-native-preview-20261019"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def original_pins():
    paths = [ORIGINAL / "manifest.json", ORIGINAL / "gates.json", ORIGINAL / "crossdoc.sqlite3",
             ROOT / ".runtime/new-knowledge-experiment-20261018/knowledge.sqlite3"]
    paths.extend(sorted((ORIGINAL / "sources").glob("*.html")))
    return {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def open_workspace():
    original = load_bundle(ORIGINAL)
    if not RUNTIME.exists():
        ensure_private_directory(RUNTIME)
    archive = RUNTIME / "archive"
    if not archive.exists():
        archive.mkdir()
        (archive / "sources").mkdir()
        for name in ("manifest.json", "gates.json"):
            shutil.copyfile(ORIGINAL / name, archive / name)
        for entry in original["manifest"]["sources"].values():
            shutil.copyfile(ORIGINAL / entry["raw_path"], archive / entry["raw_path"])
    if load_bundle(archive) != original:
        raise ValueError("ISOLATED_COPY_DIFFERS_FROM_ORIGINAL_ARCHIVE")
    source = ArchivedCrossdocSource(archive)
    source()  # Verify the complete archived sources before creating ontology state.
    seed = integration_seed(json.loads((ROOT / "config/preparation/ontology.seed.json").read_text("utf-8")))
    return CrossdocOntologyWorkspace.initialize(RUNTIME / "ontology", seed, source)


def prepare(workspace):
    candidate = workspace.submit()
    record = next(r for r in workspace.status()["candidates"] if r["id"] == candidate)
    artifact = {"prepared_at": datetime.now(timezone.utc).isoformat(), "candidate_id": candidate,
                "expected_head": workspace.status()["head"], "candidate": record["proposal"],
                "candidate_status": record["status"], "source_binding": record["source_binding"],
                "before_query": workspace.query(SCENARIO_FACTS), "original_pins": original_pins(),
                "implementation": workspace._implementation(),
                "model_calls": 0, "http_calls": 0, "native_runs": 0,
                "business_approval": False, "formally_published": False}
    path = OUTPUT / "candidate.json"
    if path.exists():
        old = json.loads(path.read_text("utf-8"))
        if old["candidate_id"] != candidate:
            raise ValueError("NEW_CANDIDATE_REQUIRES_NEW_REVIEW_ARTIFACT")
    else:
        write(path, artifact)
    return {"status": record["status"], "candidate_id": candidate, "expected_head": artifact["expected_head"],
            "candidate_path": str(path), "new_model_calls": 0, "new_http_calls": 0}


def demonstrate(workspace, candidate, baseline, reviewer):
    actor = {"kind": "human", "name": reviewer}
    old_pins = original_pins()
    before = workspace.query(SCENARIO_FACTS)
    version = workspace.review(candidate, baseline, candidate, actor, confirmed=True)
    cases = []
    try:
        for name, facts in (
            ("outside_remaining22", SCENARIO_FACTS),
            ("inside_remaining22", {**SCENARIO_FACTS, "household": "inside_province"}),
            ("outside_remaining30", {**SCENARIO_FACTS, "remaining_validity_working_days": 30}),
            ("unreviewed_unit", {**SCENARIO_FACTS, "normalized_same_start_and_unit": False}),
            ("exception_case", {**SCENARIO_FACTS, "exam_expedited": True}),
            ("other_region", {**SCENARIO_FACTS, "region": "北京市"}),
            ("unregistered_private_field", {**SCENARIO_FACTS, "document_number": "fictional-do-not-persist"}),
        ):
            cases.append({"id": name, "input_is_fictional": True,
                          "result": workspace.query(deepcopy(facts), expected_version=version)})
        # Only the new private copy is paused; the previous native archive is read-only.
        gate_path = RUNTIME / "archive/gates.json"
        saved = gate_path.read_bytes()
        try:
            gates = json.loads(saved)
            gates["sources"]["cycle"]["paused_reason"] = "isolated_demo_pause"
            write(gate_path, gates)
            try:
                workspace.query(SCENARIO_FACTS)
            except ValueError as exc:
                cases.append({"id": "source_paused", "result": {"blocked": True, "reason": str(exc)}})
            else:
                raise ValueError("PAUSED_SOURCE_WAS_NOT_BLOCKED")
        finally:
            gate_path.write_bytes(saved)
    finally:
        workspace.rollback(baseline, version, actor, confirmed=True)
    after = workspace.query(SCENARIO_FACTS)
    checks = {"before_review_blocked": before["status"] == "UNSUPPORTED_RELATION",
              "reviewed_plan_derived": cases[0]["result"]["status"] == "derived",
              "three_dependent_nodes": [r["depends_on"] for r in cases[0]["result"]["trace"]] == [[], ["c1"], ["c1", "u1"]],
              "source_parameters_used": cases[0]["result"]["usual_waiting_working_days"] == 25,
              "false_and_unknown_preserved": [r["result"].get("source_conditions_result") for r in cases[:3]] == ["false", "unknown", "unknown"],
              "invalid_unit_paused": cases[3]["result"]["status"] == "unknown",
              "exception_paused": cases[4]["result"]["status"] == "unknown",
              "region_isolated": cases[5]["result"]["status"] == "out_of_scope",
              "private_field_not_returned": cases[6]["result"]["facts"] == {} and "fictional-do-not-persist" not in json.dumps(cases),
              "source_pause_blocks": cases[7]["result"]["blocked"],
              "rollback_blocks": after["status"] == "UNSUPPORTED_RELATION",
              "original_assets_and_budget_unchanged": original_pins() == old_pins}
    report = {"status": "PASS_LOCAL_ONTOLOGY_CROSSDOC" if all(checks.values()) else "FAILED",
              "validation_scope": "Actual archived sources and fictional facts; local execution only, no native rerun.",
              "baseline": baseline, "activated_version": version, "current_head": workspace.status()["head"],
              "candidate_id": candidate, "before": before, "cases": cases, "after": after, "checks": checks,
              "original_pins": old_pins, "implementation": workspace._implementation(),
              "model_calls": 0, "http_calls": 0, "native_runs": 0, "formally_published": False}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = OUTPUT / ("demonstration-" + stamp + ".json")
    write(path, report)
    return {"status": report["status"], "checks_passed": sum(checks.values()), "checks_total": len(checks),
            "artifact": str(path), "rolled_back": report["current_head"] == baseline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="prepare", choices=("prepare", "status", "confirm-demo"))
    parser.add_argument("--candidate")
    parser.add_argument("--expected-head")
    parser.add_argument("--reviewer")
    parser.add_argument("--confirmed", action="store_true")
    args = parser.parse_args()
    if args.action == "confirm-demo" and not (args.confirmed and args.candidate and args.expected_head and args.reviewer):
        raise ValueError("EXPLICIT_HUMAN_REVIEW_AND_ROLLBACK_CONFIRMATION_REQUIRED")
    workspace = open_workspace()
    if args.action == "prepare": result = prepare(workspace)
    elif args.action == "status": result = workspace.status()
    else: result = demonstrate(workspace, args.candidate, args.expected_head, args.reviewer)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()

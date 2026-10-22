"""Reproduce JSON evidence and native receipt probes without HTTP or model calls.

Run from the repository root with .venv/Scripts/python.exe -B and this path.
The only output file is sibling native-probes.json. Existing evidence and
runtime databases are read-only; mutated events never leave memory.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import compare_model_parameters as trial
from gov_mcp.json_output import object_from_json, validate_contract
from gov_mcp.ontology_crossdoc_preview import code_fingerprints
from reassess_ontology_crossdoc_native import RecordedPreview
from verify_ontology_crossdoc_native import assess


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exact(left, right):
    """JSON containers require identical recursive Python types and values."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
    return left == right


def finals(events):
    return [e for e in events if e.get("type") == "final_answer" and e.get("depth", 0) == 0]


def parsed_final(event):
    raw = event["content"]
    return object_from_json(raw) if isinstance(raw, str) else raw


def replace(value, path, replacement):
    result = deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    old = target[path[-1]]
    target[path[-1]] = replacement
    return result, type(old).__name__


def contract_samples(directory):
    rows = read(directory / "results.json")["results"]
    cases = {case["id"]: case for case in trial.cases()}
    errors = {}
    passed = 0
    for row in rows:
        try:
            validate_contract(row["result"]["output"], trial.response_contract(cases[row["case_id"]]))
            passed += 1
        except Exception as exc:
            if not hasattr(exc, "errors"):
                raise
            for error in exc.errors():
                category = error["type"]
                errors[category] = errors.get(category, 0) + 1
    return {"stored_output_count": len(rows), "contract_passed": passed,
            "contract_rejected": len(rows) - passed, "failure_categories": errors,
            "new_model_calls": 0,
            "scope": "Revalidation of stored parsed outputs; original API response bodies are not retained here."}


def source_pin_checks(report):
    result = {}
    for name, expected in report["source_sha256"].items():
        path = ROOT / name if "/" in name else ROOT / "src/gov_mcp" / name
        result[name] = {"recorded_sha256": expected, "current_sha256": sha256(path),
                        "matches": sha256(path) == expected}
    return result


def main():
    native_root = ROOT / ".runtime/ontology-crossdoc-native-20261021"
    native_evidence = ROOT / "evidence/ontology-crossdoc-native-20261021"
    original_path = native_evidence / "20261021T133609700710Z.json"
    events_path = native_root / "20261021T133609700710Z-events.json"
    a6_root = ROOT / ".runtime/agent-autonomy-repair-20261020-a6"
    a6_public_path = ROOT / "evidence/agent-autonomy-repair-20261020-a6/result.json"
    source_files = [ROOT / p for p in (
        "src/gov_mcp/json_output.py", "scripts/verify_ontology_crossdoc_native.py",
        "scripts/reassess_ontology_crossdoc_native.py", "scripts/check_successor_autonomy_cloud.py",
        "scripts/compare_model_parameters.py")]
    protected = [original_path, events_path, native_evidence / "latest.json",
                 native_root / "crossdoc.sqlite3", native_root / "ontology-binding.json",
                 native_root / "native-drafts.json", a6_root / "native-events.json",
                 a6_root / "state/knowledge.sqlite3", a6_public_path]
    binding = read(native_root / "ontology-binding.json")
    protected.append(Path(binding["ontology_root"]) / "knowledge.sqlite3")
    for directory in ("model-parameters-20261019", "strict-json-20261019"):
        protected.extend(ROOT / "evidence" / directory / name
                         for name in ("plan.json", "results.json", "execution-fingerprints.json"))
    for number in range(1, 7):
        protected.extend([ROOT / f".runtime/agent-autonomy-repair-20261020-a{number}/native-events.json",
                          ROOT / f"evidence/agent-autonomy-repair-20261020-a{number}/result.json"])
    protected = sorted(set(protected))
    before = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in protected}

    original, events = read(original_path), read(events_path)
    roots = finals(events)
    if len(roots) != 1:
        raise ValueError("ONE_RECORDED_NANJING_ROOT_FINAL_REQUIRED")
    answer = parsed_final(roots[0])
    preview = RecordedPreview(binding, code_fingerprints())
    saved = read(native_root / "native-drafts.json")
    task = preview.get_task(answer["task_id"])
    reconstructed = preview.finalize(answer["task_id"])
    baseline = assess(events, preview, original["run_id"], saved)
    mutations = []
    for path, replacement in [
        (["reasoning", "trace", 0, "output"], 25.0),
        (["reasoning", "schema_version"], True),
        (["reasoning", "formal_approval"], 0),
        (["published"], 0),
    ]:
        changed, old_type = replace(answer, path, replacement)
        replay = deepcopy(events)
        finals(replay)[0]["content"] = json.dumps(changed, ensure_ascii=False, allow_nan=False)
        checked = assess(replay, preview, original["run_id"], saved)
        mutations.append({"path": path, "original_type": old_type,
                          "replacement_type": type(replacement).__name__,
                          "recursive_exact_match": exact(changed, task["answer"]),
                          "assess_accepted": checked["passed"],
                          "failed_checks": [k for k, passed in checked["checks"].items() if not passed]})

    a6_events, a6_public = read(a6_root / "native-events.json"), read(a6_public_path)
    a6_finals = finals(a6_events)
    if len(a6_finals) != 1:
        raise ValueError("ONE_RECORDED_SUCCESSOR_ROOT_FINAL_REQUIRED")
    a6_answer = parsed_final(a6_finals[0])
    with sqlite3.connect((a6_root / "state/knowledge.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        receipt = json.loads(db.execute("SELECT value FROM state WHERE key='final_receipt'").fetchone()[0])
    successor_mutations = []
    for path, replacement in [
        (["published"], 0),
        (["candidate", "difference", "new", "value"], 15.0),
        (["candidate", "formal_approval"], 0),
    ]:
        changed, old_type = replace(a6_answer, path, replacement)
        parsed = object_from_json(json.dumps(changed, ensure_ascii=False, allow_nan=False))
        successor_mutations.append({"path": path, "original_type": old_type,
                                   "replacement_type": type(replacement).__name__,
                                   "recursive_exact_match": exact(parsed, receipt),
                                   "receipt_equality_expression_accepted": parsed == receipt})
    attempts = []
    for number in range(1, 7):
        attempt_events = read(ROOT / f".runtime/agent-autonomy-repair-20261020-a{number}/native-events.json")
        public = read(ROOT / f"evidence/agent-autonomy-repair-20261020-a{number}/result.json")
        root_finals = finals(attempt_events)
        parsed_count, rejected_count = 0, 0
        for final in root_finals:
            try:
                if isinstance(parsed_final(final), dict):
                    parsed_count += 1
                else:
                    rejected_count += 1
            except (ValueError, TypeError):
                rejected_count += 1
        attempts.append({"attempt": number, "status": public["status"],
                         "error_code": public.get("campaign", {}).get("error_code"),
                         "root_final_count": len(root_finals), "strict_object_final_count": parsed_count,
                         "rejected_root_final_count": rejected_count,
                         "model_starts_observed": sum(e.get("type") == "model_attempt_control" and e.get("phase") == "begin" for e in attempt_events)})

    strict_report = read(ROOT / "evidence/strict-json-20261019/live-verification.json")
    strict_pins = source_pin_checks(strict_report)
    strict_evidence_pins = {
        name: {"recorded_sha256": sha, "current_sha256": sha256(ROOT / "evidence/strict-json-20261019" / name),
               "matches": sha256(ROOT / "evidence/strict-json-20261019" / name) == sha}
        for name, sha in strict_report["evidence_sha256"].items()}
    after = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in protected}
    if before != after:
        raise ValueError("PROTECTED_INPUT_BYTES_CHANGED_DURING_READ_ONLY_PROBES")
    report = {
        "status": "READ_ONLY_EVIDENCE_AND_NATIVE_TYPE_PROBES_COMPLETE",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "new_model_calls": 0, "new_http_calls": 0, "native_runs_added": 0,
        "protected_inputs_unchanged": before == after, "protected_input_sha256": before,
        "audit_source_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in source_files},
        "script_sha256": sha256(Path(__file__)),
        "sample_contracts": {
            "historical": contract_samples(ROOT / "evidence/model-parameters-20261019"),
            "strict": contract_samples(ROOT / "evidence/strict-json-20261019")},
        "strict_source_pin_checks": strict_pins, "strict_evidence_pin_checks": strict_evidence_pins,
        "nanjing": {
            "native_run_denominator": 1, "new_runs_from_offline_correction": 0,
            "private_events_hash_matches_original": sha256(events_path) == original["private_events_sha256"],
            "root_final_count": len(roots), "strict_object_parse_passed": isinstance(answer, dict),
            "actual_recursive_types_and_values_match_persisted": exact(answer, task["answer"]),
            "actual_recursive_types_and_values_match_immutable_reconstruction": exact(answer, reconstructed),
            "baseline_assess_accepted": baseline["passed"],
            "original_source_pin_checks": source_pin_checks(original), "in_memory_type_mutations": mutations},
        "successor": {
            "attempt_denominator": 6, "successful_attempts": sum(a["status"] == "PASS_BOUNDED_CLOUD_UPDATE_REPLAY" for a in attempts),
            "model_starts_observed": sum(a["model_starts_observed"] for a in attempts),
            "attempts": attempts, "a6_root_final_count": len(a6_finals),
            "a6_strict_object_parse_passed": isinstance(a6_answer, dict),
            "a6_actual_recursive_types_and_values_match_persisted": exact(a6_answer, receipt),
            "a6_public_receipt_recursive_types_and_values_match_persisted": exact(a6_public["receipt"], receipt),
            "mutation_scope": "Reproduces only parsed_final == persisted_receipt from check_successor_autonomy_cloud.py; it does not invoke the network runner or claim a complete campaign acceptance.",
            "in_memory_type_mutations": successor_mutations},
        "interpretation": [
            "Recorded successful Nanjing and a6 outputs preserve recursive types and values; probes do not indicate contamination of those real outputs.",
            "Acceptance of in-memory type mutations is a validator gap; mutated outputs are synthetic offline probes, never new real model samples.",
            "A corrected report reuses the same native run and does not increase the denominator.",
            "Historical failures remain failures; the final successful repair is not six successful independent repetitions.",
            "No long-run compliance rate, real business accuracy, current legal applicability, or live runtime code-loading state is established.",
        ],
    }
    output = Path(__file__).with_suffix(".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    print(json.dumps({"status": report["status"], "protected_inputs_unchanged": before == after,
                      "historical_contracts": report["sample_contracts"]["historical"]["contract_passed"],
                      "strict_contracts": report["sample_contracts"]["strict"]["contract_passed"],
                      "nanjing_type_mutations_accepted": sum(m["assess_accepted"] for m in mutations),
                      "successor_equality_mutations_accepted": sum(m["receipt_equality_expression_accepted"] for m in successor_mutations)}, ensure_ascii=True))


if __name__ == "__main__":
    main()

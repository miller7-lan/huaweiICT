"""Read-only replay of recorded native answers through the repaired boundary.

Requires this workstation's archived private events/databases. Never starts a
service, activates an ontology or sends a request. Refuses to overwrite output.
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

from gov_mcp.json_output import exact_json_equal, object_from_json
from gov_mcp.ontology_crossdoc_preview import code_fingerprints
from reassess_ontology_crossdoc_native import RecordedPreview
from verify_ontology_crossdoc_native import assess


def read(path):
    return object_from_json(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_final(events):
    finals = [e for e in events if e.get("type") == "final_answer" and e.get("depth", 0) == 0]
    assert len(finals) == 1
    return finals[0]


def content(event):
    raw = event["content"]
    return object_from_json(raw) if isinstance(raw, str) else raw


def changed(value, path, replacement):
    result = deepcopy(value)
    current = result
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement
    return result


def main():
    destination = Path(__file__).with_suffix(".json")
    if destination.exists():
        raise ValueError("REFUSING_TO_OVERWRITE_REPAIR_REPLAY")
    runtime = ROOT / ".runtime/ontology-crossdoc-native-20261021"
    public = ROOT / "evidence/ontology-crossdoc-native-20261021"
    successor = ROOT / ".runtime/agent-autonomy-repair-20261020-a6"
    report_path = public / "20261021T133609700710Z.json"
    events_path = runtime / "20261021T133609700710Z-events.json"
    binding_path, saved_path = runtime / "ontology-binding.json", runtime / "native-drafts.json"
    binding = read(binding_path)
    protected = [report_path, events_path, binding_path, saved_path, public / "latest.json",
                 runtime / "crossdoc.sqlite3", Path(binding["ontology_root"]) / "knowledge.sqlite3",
                 successor / "native-events.json", successor / "state/knowledge.sqlite3"]
    protected.extend(p for p in (ROOT / "evidence/json-stability-20261021").iterdir() if p.is_file())
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in protected}
    # Events are JSON arrays, whereas each model final must be a strict object.
    events = json.loads(events_path.read_text("utf-8"))
    original = read(report_path)
    assert sha(events_path) == original["private_events_sha256"]
    answer = content(root_final(events))
    preview = RecordedPreview(binding, code_fingerprints())
    baseline = assess(events, preview, original["run_id"], read(saved_path))
    assert baseline["passed"] is True
    mutations = []
    for path, replacement in [(["reasoning", "trace", 0, "output"], 25.0),
                              (["reasoning", "schema_version"], True),
                              (["reasoning", "formal_approval"], 0), (["published"], 0)]:
        replay = deepcopy(events)
        root_final(replay)["content"] = json.dumps(changed(answer, path, replacement), allow_nan=False)
        result = assess(replay, preview, original["run_id"], read(saved_path))
        assert result["passed"] is False
        mutations.append({"path": path, "accepted": result["passed"],
                          "failed_checks": [k for k, v in result["checks"].items() if not v]})
    a6_events = json.loads((successor / "native-events.json").read_text("utf-8"))
    a6_answer = content(root_final(a6_events))
    with sqlite3.connect((successor / "state/knowledge.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        receipt = object_from_json(db.execute("SELECT value FROM state WHERE key='final_receipt'").fetchone()[0])
    assert exact_json_equal(a6_answer, receipt)
    successor_mutations = []
    for path, replacement in [(["published"], 0), (["candidate", "difference", "new", "value"], 15.0),
                              (["candidate", "formal_approval"], 0)]:
        accepted = exact_json_equal(changed(a6_answer, path, replacement), receipt)
        assert accepted is False
        successor_mutations.append({"path": path, "accepted": accepted})
    after = {p.relative_to(ROOT).as_posix(): sha(p) for p in protected}
    assert before == after
    sources = ("src/gov_mcp/json_output.py", "scripts/verify_ontology_crossdoc_native.py",
               "scripts/check_successor_autonomy_cloud.py", "scripts/reassess_ontology_crossdoc_native.py")
    report = {"status": "PASS_OFFLINE_REPAIRED_NATIVE_BOUNDARY", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "new_model_calls": 0, "new_http_calls": 0, "new_native_runs": 0,
              "nanjing_recorded_answer_current_assess_passed": baseline["passed"],
              "nanjing_type_mutations": mutations, "a6_recorded_answer_exactly_matches_receipt": True,
              "a6_type_mutations": successor_mutations,
              "a6_scope": "Current comparison expression only; full driver rejection uses fabricated regression fixtures.",
              "protected_inputs_unchanged": before == after, "protected_input_sha256": before,
              "current_source_sha256": {name: sha(ROOT / name) for name in sources},
              "script_sha256": sha(Path(__file__)),
              "limitation": "Offline replay does not establish new real-model compliance or live service code-loading state."}
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    print(json.dumps({"status": report["status"], "nanjing_mutations_rejected": len(mutations),
                      "a6_comparison_mutations_rejected": len(successor_mutations), "protected_inputs_unchanged": True}))


if __name__ == "__main__":
    main()

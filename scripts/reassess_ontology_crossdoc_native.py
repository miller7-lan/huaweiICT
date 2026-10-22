"""Read-only correction of a native event-contract false negative; no rerun.

The failed original report, consumed campaign and rolled-back ontology remain
unchanged. A separate report validates the exact saved trace and immutable plan.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS, load_bundle, solve
from gov_mcp.knowledge.store import Store, digest
from gov_mcp.ontology_crossdoc import ArchivedCrossdocSource, CrossdocOntologyWorkspace, PROFILE
from gov_mcp.ontology_crossdoc_preview import code_fingerprints
from prepare_ontology_crossdoc_native import RUNTIME, OUTPUT
from provision_crossdoc_native_preview import read, write
from verify_ontology_crossdoc_native import assess


class ReadOnlyStore(Store):
    def __init__(self, root):
        self.root = Path(root)
        self.db_path = self.root / "knowledge.sqlite3"

    @contextmanager
    def db(self, write=False):
        if write:
            raise ValueError("OFFLINE_REASSESSMENT_NEVER_WRITES_RUNTIME")
        db = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            yield db
        finally:
            db.close()


class RecordedPreview:
    """Only immutable recorded-answer reconstruction; never a live MCP preview."""
    def __init__(self, binding, bridge_hashes):
        self.expected_version = binding["expected_version"]
        self.binding, self.bridge_hashes = binding, bridge_hashes
        self.workspace = CrossdocOntologyWorkspace.__new__(CrossdocOntologyWorkspace)
        self.workspace.root = Path(binding["ontology_root"])
        self.workspace.store = ReadOnlyStore(self.workspace.root)
        self.workspace.source_reader = ArchivedCrossdocSource(RUNTIME)

    def get_task(self, task_id):
        with sqlite3.connect((RUNTIME / "crossdoc.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("RECORDED_TASK_REQUIRED")
        return json.loads(row[0])

    def finalize(self, task_id):
        task = self.get_task(task_id)
        if task["status"] != "complete" or task["facts"] != SCENARIO_FACTS:
            raise ValueError("RECORDED_FIXED_COMPLETED_TASK_REQUIRED")
        raw = load_bundle(RUNTIME)
        version = self.workspace._version(self.expected_version)
        current = self.workspace._current()
        self.workspace._guard_version(version, current)
        instance = version["data"]["instances"][0]
        proposal = instance["proposal"]
        relation = next(r for r in version["data"]["seed"]["relation_types"]
                        if r["name"] == proposal["relation_type"]["name"])
        values, trace = self.workspace._execute(relation, proposal["source_documents"], deepcopy(SCENARIO_FACTS))
        context = {"profile": PROFILE, "version": self.expected_version,
                   "candidate_id": instance["candidate_id"], "source_binding": current["binding"],
                   "implementation": self.workspace._implementation()}
        context["binding"] = digest(context)
        for hop in trace:
            hop["ontology_context_binding"] = context["binding"]
        proof = solve({}, deepcopy(SCENARIO_FACTS))
        proof.update(status="derived", reason="REVIEWED_ONTOLOGY_PLAN_EXECUTED", version=self.expected_version,
                     conclusion="cannot_establish_full_cycle_coverage" if values["j1"] == "false" else "coverage_not_disproved",
                     source_conditions_result=values["j1"], usual_waiting_working_days=values["c1"],
                     old_card_interval_working_days=values["u1"], trace=trace, ontology_context=context)
        answer = {"task_id": task_id, "mode": "reviewed_ontology_crossdoc_archived_preview",
                  "published": False, "formal_approval": False,
                  "source_binding": digest({"archive": raw["binding"], "ontology": context, "bridge": self.bridge_hashes}),
                  "archive_binding": raw["binding"], "ontology_context": context,
                  "verification_method": "reviewed_ontology_plan_execution", "reasoning": proof}
        if (task["reasoning"] != proof or task["reasoning_sha256"] != digest(proof)
                or task["answer"] != answer or task["answer_sha256"] != digest(answer)):
            raise ValueError("RECORDED_ANSWER_DIFFERS_FROM_IMMUTABLE_PLAN")
        return answer


def main():
    latest = read(OUTPUT / "latest.json")
    original_path = OUTPUT / latest["evidence"]
    original = read(original_path)
    if [k for k, ok in original.get("checks", {}).items() if not ok] != ["child_call_counts"]:
        raise ValueError("ONLY_IDENTIFIED_EVENT_CONTRACT_FALSE_NEGATIVE_CAN_BE_CORRECTED")
    events_path = RUNTIME / (latest["evidence"][:-5] + "-events.json")
    if hashlib.sha256(events_path.read_bytes()).hexdigest() != original["private_events_sha256"]:
        raise ValueError("ORIGINAL_EVENT_BYTES_CHANGED")
    bridge = code_fingerprints()
    if any(original["source_sha256"].get(name) != sha for name, sha in bridge.items()):
        raise ValueError("EXECUTED_BRIDGE_SOURCE_CHANGED")
    binding = read(RUNTIME / "ontology-binding.json")
    protected = [RUNTIME / "crossdoc.sqlite3", Path(binding["ontology_root"]) / "knowledge.sqlite3", original_path, OUTPUT / "latest.json"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    preview = RecordedPreview(binding, bridge)
    report = assess(read(events_path), preview, latest["run_id"], read(RUNTIME / "native-drafts.json"))
    head = preview.workspace.store.get("state", "active_rules")
    report.update(recorded_at=datetime.now(timezone.utc).isoformat(),
                  status="PASS_NATIVE_RECORDED_TRACE_CORRECTION" if report["passed"] else "FAILED_OFFLINE_REASSESSMENT",
                  correction="Nexent emits subagent_start/end and child tool invocation_id; wrapper tools need not be emitted.",
                  original_evidence=latest["evidence"], original_evidence_sha256=hashlib.sha256(original_path.read_bytes()).hexdigest(),
                  original_failed_report_preserved=True, original_campaign_status=original["campaign"]["run_status"],
                  campaign_ledger_rewritten=False, native_runs_total=1, new_native_runs=0,
                  new_model_calls=0, new_http_calls=0, ontology_head=head, rolled_back=head == binding["baseline"],
                  duration_ms_of_original_run=original["duration_ms"], model_starts_of_original_run=original["observed_model_starts"],
                  runtime_bytes_unchanged=before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected})
    report["passed"] = report["passed"] and report["rolled_back"] and report["runtime_bytes_unchanged"]
    report["status"] = "PASS_NATIVE_RECORDED_TRACE_CORRECTION" if report["passed"] else "FAILED_OFFLINE_REASSESSMENT"
    write(OUTPUT / "offline-event-contract-correction.json", report)
    print(json.dumps({k: report[k] for k in ("status", "passed", "checks", "rolled_back", "runtime_bytes_unchanged", "new_model_calls")}, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

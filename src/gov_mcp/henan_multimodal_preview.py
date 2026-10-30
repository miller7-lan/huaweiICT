"""One source-bound native campaign for the reviewed Henan archive plan.

The existing authenticated MCP stages and one-run ledger are reused. This
bridge has no review, fetch, OCR, model, or business-publication operation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from gov_mcp.crossdoc_preview import PreviewError
from gov_mcp.henan_multimodal import FACTS, PROFILE
from gov_mcp.json_output import exact_json_equal
from gov_mcp.knowledge.store import digest
from gov_mcp.ontology_crossdoc_preview import OntologyCrossdocPreview
from gov_mcp.ontology_crossdoc_preview import code_fingerprints as crossdoc_fingerprints


HENAN_SCENARIO = "henan_expiry_renewal_archive"


def code_fingerprints():
    names = ("henan_multimodal_preview.py", "henan_multimodal.py",
             "henan_multimodal_sources.py", "json_output.py", "knowledge/store.py",
             "knowledge_fetch.py")
    base = Path(__file__).parent
    return {**crossdoc_fingerprints(),
            **{name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}}


class HenanMultimodalPreview(OntologyCrossdocPreview):
    fingerprints = staticmethod(code_fingerprints)

    def __init__(self, root, workspace, expected_version):
        super().__init__(root, workspace, expected_version, self._load_archive)

    def _load_archive(self, _root):
        current = self.workspace._current()
        return {"documents": current["candidate"]["source_documents"],
                "binding": current["binding"]}

    def _bundle(self):
        raw = self.loader(self.root)
        if set(raw["documents"]) != {"catalog", "norm", "scan"}:
            raise PreviewError("EXACT_THREE_SOURCE_BUNDLE_REQUIRED")
        proof = self.workspace.query(deepcopy(FACTS), expected_version=self.expected_version)
        context = proof.get("ontology_context", {})
        trace = proof.get("trace", [])
        if (proof.get("status") != "derived_archive" or context.get("profile") != PROFILE
                or context.get("version") != self.expected_version
                or context.get("source_binding") != raw["binding"]
                or [node.get("id") for node in trace] !=
                   ["catalog_basis", "fee_branch", "service_archive", "archive_answer"]
                or any(node.get("ontology_context_binding") != context.get("binding") for node in trace)
                or proof.get("formal_approval") is not False
                or proof.get("formally_published") is not False
                or proof.get("current_applicability") != "unknown"
                or proof.get("business_conclusion_status") != "PAUSED_UNREVIEWED"
                or proof.get("actual_fee_yuan") is not None or proof.get("promised_duration") is not None):
            raise PreviewError("ACTIVE_REVIEWED_SOURCE_BOUND_ONTOLOGY_REQUIRED")
        return {**raw, "archive_binding": raw["binding"], "reviewed_reasoning": proof,
                "ontology_context": context,
                "binding": digest({"archive": raw["binding"], "ontology": context,
                                   "bridge": self.fingerprints()})}

    def preflight(self):
        bundle = self._bundle()
        return {"binding": bundle["binding"], "archive_binding": bundle["archive_binding"],
                "ontology_context": bundle["ontology_context"], "document_count": 3,
                "profile": PROFILE, "scenario_id": HENAN_SCENARIO,
                "mode": "reviewed_henan_multimodal_archived_preview"}

    def _context(self, task_id):
        bundle = self._bundle()
        task = self.get_task(task_id)
        if not exact_json_equal(task.get("facts"), FACTS) or task.get("scenario_id") != HENAN_SCENARIO:
            raise PreviewError("FIXED_TASK_INPUT_CHANGED")
        with self.db() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (task["run_id"],)).fetchone()
        if row is None or (row[0] != "active" and not (row[0] == "passed" and task["status"] == "complete")):
            raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
        if task["source_binding"] != bundle["binding"] or task["code_sha256"] != self.fingerprints():
            raise PreviewError("SOURCE_OR_IMPLEMENTATION_CHANGED")
        return task, bundle

    def open_query(self, scenario_id):
        if scenario_id != HENAN_SCENARIO:
            raise PreviewError("FIXED_SCENARIO_REQUIRED")
        bundle = self._bundle()
        with self.db(True) as db:
            rows = list(db.execute("SELECT id FROM runs WHERE status='active'"))
            if len(rows) != 1:
                raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
            if db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
                raise PreviewError("TASK_ALREADY_OPENED")
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_DURING_STAGE")
            task = {"task_id": uuid.uuid4().hex, "run_id": rows[0][0], "status": "opened",
                    "scenario_id": scenario_id, "facts": deepcopy(FACTS), "answer": None,
                    "source_binding": bundle["binding"], "code_sha256": self.fingerprints()}
            db.execute("INSERT INTO tasks VALUES(?,?,?)",
                       (task["task_id"], task["run_id"], json.dumps(task, ensure_ascii=False)))
        return {"task_id": task["task_id"], "status": "opened", "scenario_id": scenario_id,
                "facts": task["facts"], "source_binding": bundle["binding"], "published": False}

    def finalize(self, task_id):
        task, bundle = self._context(task_id)
        if task["status"] not in {"verified", "complete"}:
            raise PreviewError("FINALIZATION_STAGE_REQUIRED")
        proof = bundle["reviewed_reasoning"]
        if not exact_json_equal(task["reasoning"], proof) or task["reasoning_sha256"] != digest(proof):
            raise PreviewError("REVIEWED_PROOF_CHANGED_BEFORE_RETURN")
        answer = {"task_id": task_id, "mode": "reviewed_henan_multimodal_archived_preview",
                  "profile": PROFILE, "scenario_id": HENAN_SCENARIO,
                  "published": False, "formal_approval": False, "source_binding": bundle["binding"],
                  "archive_binding": bundle["archive_binding"], "ontology_context": bundle["ontology_context"],
                  "verification_method": "reviewed_ontology_plan_execution", "reasoning": proof}
        with self.db(True) as db:
            self._stage_task(db, task, allow_completed=True)
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_BEFORE_RETURN")
            if task["status"] == "complete":
                if not exact_json_equal(task["answer"], answer) or task.get("answer_sha256") != digest(answer):
                    raise PreviewError("PERSISTED_ANSWER_CHANGED")
            else:
                task.update(status="complete", answer=answer, answer_sha256=digest(answer))
                self._put(db, task)
        return answer

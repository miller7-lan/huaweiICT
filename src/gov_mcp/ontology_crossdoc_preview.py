"""Single native campaign whose proof executes the reviewed ontology version.

The earlier cross-document campaign is never opened or reset. This adapter has
no review, model, source-fetch or business-publication operation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from gov_mcp.crossdoc_preview import CrossdocPreview, PreviewError
from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS
from gov_mcp.knowledge.store import digest


SERVICE = "govkb_ontology_crossdoc_preview"
SKILL = "govkb-ontology-crossdoc-preview"
TOOLS = {"govkb_ontology_crossdoc_" + stage for stage in
         ("open_query", "retrieve", "verify", "state", "finalize")}


def code_fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("ontology_crossdoc_preview.py", "ontology_crossdoc_preview_server.py",
                         "crossdoc_preview.py", "crossdoc_reasoning.py", "ontology_crossdoc.py",
                         "ontology_evolution.py", "ontology_seed.py")}


class OntologyCrossdocPreview(CrossdocPreview):
    def __init__(self, root, workspace, expected_version, bundle_loader=None):
        if not isinstance(expected_version, str) or len(expected_version) != 64:
            raise PreviewError("EXPLICIT_REVIEWED_ONTOLOGY_VERSION_REQUIRED")
        self.workspace, self.expected_version = workspace, expected_version
        super().__init__(root, bundle_loader)

    def _bundle(self):
        raw = super()._bundle()
        proof = self.workspace.query(deepcopy(SCENARIO_FACTS), expected_version=self.expected_version)
        context = proof.get("ontology_context", {})
        if (proof.get("status") != "derived" or context.get("version") != self.expected_version
                or context.get("source_binding") != raw["binding"]
                or any(hop.get("ontology_context_binding") != context.get("binding")
                       for hop in proof.get("trace", []))):
            raise PreviewError("ACTIVE_REVIEWED_SOURCE_BOUND_ONTOLOGY_REQUIRED")
        # Every stage, including the final return, rechecks the current source,
        # ontology head and executable implementation through workspace.query.
        return {**raw, "archive_binding": raw["binding"], "reviewed_reasoning": proof,
                "ontology_context": context,
                "binding": digest({"archive": raw["binding"], "ontology": context,
                                   "bridge": code_fingerprints()})}

    def preflight(self):
        bundle = self._bundle()
        return {"binding": bundle["binding"], "archive_binding": bundle["archive_binding"],
                "ontology_context": bundle["ontology_context"], "document_count": 2,
                "mode": "reviewed_ontology_crossdoc_archived_preview"}

    @staticmethod
    def _stage_task(db, expected, allow_completed=False):
        row = db.execute("SELECT t.payload,t.run_id,r.status FROM tasks t "
                         "JOIN runs r ON t.run_id=r.id WHERE t.id=?",
                         (expected["task_id"],)).fetchone()
        if row is None or json.loads(row[0]) != expected or row[1] != expected["run_id"]:
            raise PreviewError("TASK_CHANGED_DURING_STAGE")
        if row[2] != "active" and not (allow_completed and row[2] == "passed"
                                       and expected["status"] == "complete"):
            raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")

    def retrieve(self, task_id):
        task, bundle = self._context(task_id)
        if task["status"] != "opened":
            raise PreviewError("RETRIEVAL_STAGE_REQUIRED")
        with self.db(True) as db:
            self._stage_task(db, task)
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_DURING_STAGE")
            task.update(status="retrieved", retrieval_sha256=digest(bundle["documents"]))
            self._put(db, task)
        return {"task_id": task_id, "status": "retrieved", "documents": bundle["documents"],
                "facts": task["facts"], "source_binding": bundle["binding"], "published": False}

    def verify(self, task_id):
        task, bundle = self._context(task_id)
        if task["status"] != "retrieved":
            raise PreviewError("VERIFICATION_STAGE_REQUIRED")
        if task["retrieval_sha256"] != digest(bundle["documents"]):
            raise PreviewError("RETRIEVAL_BINDING_CHANGED")
        proof = bundle["reviewed_reasoning"]
        with self.db(True) as db:
            self._stage_task(db, task)
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_DURING_STAGE")
            task.update(status="verified", reasoning=proof, reasoning_sha256=digest(proof))
            self._put(db, task)
        return {"task_id": task_id, "status": "verified", "reasoning": proof,
                "verification_method": "reviewed_ontology_plan_execution", "published": False}

    def state(self, task_id):
        task, bundle = self._context(task_id)
        with self.db(True) as db:
            self._stage_task(db, task, allow_completed=True)
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_DURING_STAGE")
        return {"task_id": task_id, "status": task["status"],
                "can_finalize": task["status"] == "verified", "complete": task["status"] == "complete",
                "source_binding": task["source_binding"]}

    def finalize(self, task_id):
        task, bundle = self._context(task_id)
        if task["status"] not in {"verified", "complete"}:
            raise PreviewError("FINALIZATION_STAGE_REQUIRED")
        proof = bundle["reviewed_reasoning"]
        if task["reasoning"] != proof or task["reasoning_sha256"] != digest(proof):
            raise PreviewError("REVIEWED_PROOF_CHANGED_BEFORE_RETURN")
        answer = {"task_id": task_id, "mode": "reviewed_ontology_crossdoc_archived_preview",
                  "published": False, "formal_approval": False, "source_binding": bundle["binding"],
                  "archive_binding": bundle["archive_binding"],
                  "ontology_context": bundle["ontology_context"],
                  "verification_method": "reviewed_ontology_plan_execution", "reasoning": proof}
        with self.db(True) as db:
            self._stage_task(db, task, allow_completed=True)
            if self._bundle()["binding"] != bundle["binding"]:
                raise PreviewError("ONTOLOGY_OR_SOURCE_CHANGED_BEFORE_RETURN")
            if task["status"] == "complete":
                if task["answer"] != answer or task.get("answer_sha256") != digest(answer):
                    raise PreviewError("PERSISTED_ANSWER_CHANGED")
            else:
                task.update(status="complete", answer=answer, answer_sha256=digest(answer))
                self._put(db, task)
        return answer

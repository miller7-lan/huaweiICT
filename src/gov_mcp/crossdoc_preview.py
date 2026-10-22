"""One native campaign and a source-bound cross-document engineering proof."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from gov_mcp.knowledge.store import digest


SERVICE = "govkb_crossdoc_preview"
SKILL = "govkb-crossdoc-preview"
SCENARIO = "nanjing_outside_old_card"
TOOLS = {"govkb_crossdoc_open_query", "govkb_crossdoc_retrieve", "govkb_crossdoc_verify",
         "govkb_crossdoc_state", "govkb_crossdoc_finalize"}


class PreviewError(ValueError):
    pass


def code_fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("crossdoc_preview.py", "crossdoc_preview_server.py", "crossdoc_reasoning.py")}


class CrossdocPreview:
    def __init__(self, root, bundle_loader=None):
        from gov_mcp.crossdoc_reasoning import load_bundle
        self.root = Path(root)
        self.loader = bundle_loader or load_bundle
        self.loader(self.root)  # Must have an admitted source manifest before creating state.
        self.path = self.root / "crossdoc.sqlite3"
        with self.db(True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,failure TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,payload TEXT NOT NULL);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            if write: db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _bundle(self):
        bundle = self.loader(self.root)
        if set(bundle["documents"]) != {"cycle", "old_card"}:
            raise PreviewError("EXACT_TWO_SOURCE_BUNDLE_REQUIRED")
        if not isinstance(bundle.get("binding"), str) or len(bundle["binding"]) != 64:
            raise PreviewError("SOURCE_BINDING_REQUIRED")
        return bundle

    def preflight(self):
        bundle = self._bundle()
        return {"binding": bundle["binding"], "document_count": 2, "mode": "crossdoc_archived_preview"}

    def counts(self):
        with self.db() as db:
            runs = list(db.execute("SELECT status FROM runs"))
            return {"native_runs": len(runs), "max_native_runs": 1,
                    "tasks": db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    "run_status": runs[0][0] if runs else None, "jev_calls": 0, "translation_calls": 0}

    def begin_run(self):
        self.preflight()
        with self.db(True) as db:
            if db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]:
                raise PreviewError("NATIVE_CAMPAIGN_BUDGET_EXHAUSTED")
            identifier = uuid.uuid4().hex
            db.execute("INSERT INTO runs VALUES(?,?,NULL)", (identifier, "active"))
        return identifier

    def end_run(self, run_id, passed, failure=None):
        if type(passed) is not bool: raise PreviewError("EXPLICIT_RUN_OUTCOME_REQUIRED")
        with self.db(True) as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None: raise PreviewError("RUN_NOT_FOUND")
            if row[0] != "active": raise PreviewError("RUN_ALREADY_TERMINAL")
            if passed:
                tasks = [json.loads(item[0]) for item in db.execute("SELECT payload FROM tasks WHERE run_id=?", (run_id,))]
                if len(tasks) != 1 or tasks[0]["status"] != "complete":
                    raise PreviewError("COMPLETED_TASK_REQUIRED")
            db.execute("UPDATE runs SET status=?,failure=? WHERE id=?", ("passed" if passed else "blocked", failure, run_id))

    @staticmethod
    def _put(db, task):
        db.execute("UPDATE tasks SET payload=? WHERE id=?",
                   (json.dumps(task, ensure_ascii=False, allow_nan=False), task["task_id"]))

    def get_task(self, task_id):
        if not isinstance(task_id, str) or len(task_id) != 32:
            raise PreviewError("TASK_NOT_FOUND")
        with self.db() as db:
            row = db.execute("SELECT payload,run_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row: raise PreviewError("TASK_NOT_FOUND")
            task = json.loads(row[0])
            if task.get("task_id") != task_id or task.get("run_id") != row[1]:
                raise PreviewError("TASK_IDENTITY_CHANGED")
            return task

    def _context(self, task_id):
        from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS
        bundle = self._bundle()
        task = self.get_task(task_id)
        if task.get("facts") != SCENARIO_FACTS or task.get("scenario_id") != SCENARIO:
            raise PreviewError("FIXED_TASK_INPUT_CHANGED")
        with self.db() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (task["run_id"],)).fetchone()
        if row is None or (row[0] != "active" and not (row[0] == "passed" and task["status"] == "complete")):
            raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
        if task["source_binding"] != bundle["binding"] or task["code_sha256"] != code_fingerprints():
            raise PreviewError("SOURCE_OR_IMPLEMENTATION_CHANGED")
        return task, bundle

    def open_query(self, scenario_id):
        from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS
        if scenario_id != SCENARIO: raise PreviewError("FIXED_SCENARIO_REQUIRED")
        bundle = self._bundle()
        with self.db(True) as db:
            rows = list(db.execute("SELECT id FROM runs WHERE status='active'"))
            if len(rows) != 1: raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
            if db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
                raise PreviewError("TASK_ALREADY_OPENED")
            task = {"task_id": uuid.uuid4().hex, "run_id": rows[0][0], "status": "opened",
                    "scenario_id": scenario_id, "facts": deepcopy(SCENARIO_FACTS), "answer": None,
                    "source_binding": bundle["binding"], "code_sha256": code_fingerprints()}
            db.execute("INSERT INTO tasks VALUES(?,?,?)", (task["task_id"], task["run_id"], json.dumps(task, ensure_ascii=False)))
        return {"task_id": task["task_id"], "status": "opened", "scenario_id": scenario_id,
                "facts": task["facts"], "source_binding": bundle["binding"], "published": False}

    def retrieve(self, task_id):
        task, bundle = self._context(task_id)
        if task["status"] != "opened": raise PreviewError("RETRIEVAL_STAGE_REQUIRED")
        with self.db(True) as db:
            latest = json.loads(db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            if latest != task: raise PreviewError("TASK_CHANGED_DURING_STAGE")
            if self._bundle()["binding"] != bundle["binding"]: raise PreviewError("SOURCE_CHANGED_DURING_STAGE")
            task.update(status="retrieved", retrieval_sha256=digest(bundle["documents"]))
            self._put(db, task)
        return {"task_id": task_id, "status": "retrieved", "documents": bundle["documents"],
                "facts": task["facts"], "source_binding": bundle["binding"], "published": False}

    def verify(self, task_id):
        from gov_mcp.crossdoc_reasoning import solve
        task, bundle = self._context(task_id)
        if task["status"] != "retrieved": raise PreviewError("VERIFICATION_STAGE_REQUIRED")
        if task["retrieval_sha256"] != digest(bundle["documents"]): raise PreviewError("RETRIEVAL_BINDING_CHANGED")
        reasoning = solve(bundle["documents"], task["facts"])
        if reasoning["status"] != "derived": raise PreviewError("CROSS_DOCUMENT_PROOF_INCOMPLETE")
        with self.db(True) as db:
            latest = json.loads(db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            if latest != task: raise PreviewError("TASK_CHANGED_DURING_STAGE")
            if self._bundle()["binding"] != bundle["binding"]: raise PreviewError("SOURCE_CHANGED_DURING_STAGE")
            task.update(status="verified", reasoning=reasoning, reasoning_sha256=digest(reasoning))
            self._put(db, task)
        return {"task_id": task_id, "status": "verified", "verification_method": "deterministic_source_bound",
                "reasoning": reasoning, "published": False}

    def state(self, task_id):
        task, _ = self._context(task_id)
        return {"task_id": task_id, "status": task["status"], "can_finalize": task["status"] == "verified",
                "complete": task["status"] == "complete", "source_binding": task["source_binding"]}

    def finalize(self, task_id):
        from gov_mcp.crossdoc_reasoning import solve
        task, bundle = self._context(task_id)
        if task["status"] not in {"verified", "complete"}: raise PreviewError("FINALIZATION_STAGE_REQUIRED")
        reasoning = solve(bundle["documents"], task["facts"])
        if task["reasoning"] != reasoning or task["reasoning_sha256"] != digest(reasoning):
            raise PreviewError("PROOF_CHANGED_BEFORE_RETURN")
        answer = {"task_id": task_id, "mode": "crossdoc_archived_preview", "published": False, "formal_approval": False,
                  "source_binding": bundle["binding"], "verification_method": "deterministic_source_bound",
                  "reasoning": reasoning}
        if self._bundle()["binding"] != bundle["binding"]: raise PreviewError("SOURCE_CHANGED_BEFORE_RETURN")
        with self.db(True) as db:
            latest = json.loads(db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            if latest != task: raise PreviewError("TASK_CHANGED_BEFORE_RETURN")
            if task["status"] == "complete":
                if task["answer"] != answer or task.get("answer_sha256") != digest(answer):
                    raise PreviewError("PERSISTED_ANSWER_CHANGED")
            else:
                task.update(status="complete", answer=answer, answer_sha256=digest(answer))
                self._put(db, task)
        return answer

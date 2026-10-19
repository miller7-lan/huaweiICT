"""Bounded native orchestration over the real-source, unpublished matter version."""
from copy import deepcopy
import json
from pathlib import Path
import time
import uuid

from gov_mcp.knowledge.store import Store
from gov_mcp.matter_graph import canonical_hash, extract_candidates, scope_check
from gov_mcp.matter_lifecycle import Principal

SERVICE = "govmatter_native_preview"
SKILL = "govmatter-source-proof"
TOOLS = {"govmatter_open", "govmatter_retrieve", "govmatter_verify", "govmatter_state", "govmatter_finalize"}
FACTS = {"region": "南京市", "matter": "resident-id-expiry-renewal", "adult": True,
    "self": True, "civil_capacity": "full", "household": "jiangsu", "reason": "expiry",
    "old_card_valid": True}
SCENARIO = "nanjing-adult-valid-old-card"
ACTOR = Principal("native-fixed-public-scenario", "citizen", "service")


class NativeMatterError(ValueError):
    pass


class MatterNative:
    def __init__(self, root, lifecycle, *, clock=time.time):
        self.root, self.lifecycle, self.clock = Path(root), lifecycle, clock
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if manifest != {"schema": 1, "purpose": SERVICE, "native_run_limit": 2,
                        "scenario_id": SCENARIO, "formal_publication": False}:
            raise NativeMatterError("CAMPAIGN_MANIFEST_MISMATCH")
        self.store = Store(self.root / "stages", clock=clock)

    def begin(self, repair_note=None):
        with self.store.db(True) as db:
            campaign = self.store.get("state", "campaign", db) or {"attempts": [], "active": None, "closed": False}
            if campaign["active"] or campaign["closed"] or len(campaign["attempts"]) >= 2:
                raise NativeMatterError("CAMPAIGN_NOT_AVAILABLE")
            if campaign["attempts"] and (not isinstance(repair_note, str) or len(repair_note.strip()) < 12):
                raise NativeMatterError("CONCRETE_REPAIR_NOTE_REQUIRED")
            run_id = "native-" + uuid.uuid4().hex
            campaign["attempts"].append({"id": run_id, "started_at": self.clock(), "repair_note": repair_note})
            campaign["active"] = run_id
            self.store.put("state", "campaign", campaign, db)
            return run_id

    def finish(self, run_id, *, passed, external_failure=False):
        with self.store.db(True) as db:
            campaign = self.store.get("state", "campaign", db)
            if not campaign or campaign["active"] != run_id:
                raise NativeMatterError("ACTIVE_RUN_MISMATCH")
            campaign["attempts"][-1].update(passed=bool(passed), finished_at=self.clock(), external_failure=bool(external_failure))
            campaign.update(active=None, closed=bool(passed or external_failure or len(campaign["attempts"]) >= 2))
            self.store.put("state", "campaign", campaign, db)

    def _active(self, db=None):
        campaign = self.store.get("state", "campaign", db)
        if not campaign or not campaign["active"] or self.clock() - campaign["attempts"][-1]["started_at"] > 600:
            raise NativeMatterError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
        return campaign["active"]

    def task(self, task_id):
        if not isinstance(task_id, str) or not task_id.startswith("task-") or len(task_id) != 37:
            raise NativeMatterError("INVALID_TASK_ID")
        task = self.store.get("runs", task_id)
        if not task:
            raise NativeMatterError("TASK_NOT_FOUND")
        return task

    def _snapshot(self, task):
        if task["run_id"] != self._active():
            raise NativeMatterError("TASK_OUTSIDE_ACTIVE_RUN")
        record = self.lifecycle._version(task["version"])
        self.lifecycle._guard(record, set(record["source_identities"]))
        return self.lifecycle._load(record)

    def _commit(self, task, old_status, patch):
        self._snapshot(task)
        with self.store.db(True) as db:
            if task["run_id"] != self._active(db):
                raise NativeMatterError("TASK_OUTSIDE_ACTIVE_RUN")
            current = self.store.get("runs", task["task_id"], db)
            if current["status"] != old_status:
                raise NativeMatterError("STAGE_CHANGED")
            current.update(patch)
            self.store.put("runs", task["task_id"], current, db)
            self.store.audit("matter_native_" + patch.get("status", old_status),
                {"task_id": task["task_id"], "run_id": task["run_id"], "version": task["version"]}, db)
            return current

    def open(self, scenario_id):
        if scenario_id != SCENARIO:
            raise NativeMatterError("FIXED_PUBLIC_SCENARIO_REQUIRED")
        run_id = self._active()
        version = self.lifecycle.head()
        task = {"task_id": "task-" + uuid.uuid4().hex, "run_id": run_id,
            "scenario_id": scenario_id, "version": version, "facts": deepcopy(FACTS), "status": "opened"}
        pack = self._snapshot(task)
        if scope_check(pack, FACTS)["status"] != "IN_SCOPE":
            raise NativeMatterError("SCENARIO_SCOPE_REJECTED")
        with self.store.db(True) as db:
            if self._active(db) != run_id:
                raise NativeMatterError("ACTIVE_RUN_MISMATCH")
            if db.execute("SELECT 1 FROM runs WHERE json_extract(payload,'$.run_id')=?", (run_id,)).fetchone():
                raise NativeMatterError("ONE_TASK_PER_RUN")
            self.store.put("runs", task["task_id"], task, db)
        return {k: task[k] for k in ("task_id", "scenario_id", "version", "status")}

    def retrieve(self, task_id):
        task = self.task(task_id); pack = self._snapshot(task)
        if task["status"] in ("retrieved", "verified", "complete"):
            return deepcopy(task["retrieval"])
        if task["status"] != "opened":
            raise NativeMatterError("RETRIEVAL_ORDER_REQUIRED")
        # These are exact source-bound candidates, not LLM-generated relationships.
        candidates = extract_candidates(pack)["candidates"]
        selected = {e["segment_id"] for c in candidates for e in c["evidence"]}
        segments = [{"source_id": s["source_id"], "url": s["url"], "title": s["title"],
            "published_at": s["published_at"], "effective_from": s["effective_from"], **row}
            for s in pack["sources"] for row in s["segments"] if row["segment_id"] in selected]
        retrieval = {"task_id": task_id, "version": task["version"], "segments": segments,
            "retrieval_sha256": canonical_hash(segments), "knowledge_published": False}
        self._commit(task, "opened", {"status": "retrieved", "retrieval": retrieval})
        return deepcopy(retrieval)

    def verify(self, task_id):
        task = self.task(task_id); self._snapshot(task)
        if task["status"] in ("verified", "complete"):
            return {"task_id": task_id, "status": "verified", "verification": deepcopy(task["verification"])}
        if task["status"] != "retrieved":
            raise NativeMatterError("RETRIEVAL_REQUIRED")
        retrieval = task["retrieval"]
        if canonical_hash(retrieval["segments"]) != retrieval["retrieval_sha256"]:
            raise NativeMatterError("RETRIEVAL_INTEGRITY_FAILURE")
        result = self.lifecycle.query(task["facts"], principal=ACTOR, version_id=task["version"])
        if result.get("status") != "DRAFT_PROOF_READY":
            raise NativeMatterError("SUPPORTED_MULTI_DOCUMENT_PROOF_REQUIRED")
        coverage = {(row["source_id"], row["segment_id"]) for row in retrieval["segments"]}
        evidence = [e for proof in result["proofs"] for e in proof["evidence"]]
        if not evidence or any((e["source_id"], e["segment_id"]) not in coverage for e in evidence):
            raise NativeMatterError("RETRIEVAL_EVIDENCE_COVERAGE_FAILURE")
        verification = {"method": "exact_original_citation_and_typed_program_proof", "jev_called": False,
            "source_documents": sorted({e["source_id"] for e in evidence}),
            "human_review": "pending", "knowledge_published": False}
        self._commit(task, "retrieved", {"status": "verified", "result": result, "verification": verification})
        return {"task_id": task_id, "status": "verified", "verification": deepcopy(verification)}

    def state(self, task_id):
        task = self.task(task_id); self._snapshot(task)
        return {"task_id": task_id, "status": task["status"], "version": task["version"],
            "can_finalize": task["status"] in ("verified", "complete"), "knowledge_published": False}

    def finalize(self, task_id):
        task = self.task(task_id); self._snapshot(task)
        if task["status"] not in ("verified", "complete"):
            raise NativeMatterError("VERIFICATION_REQUIRED")
        result = self.lifecycle.query(task["facts"], principal=ACTOR, version_id=task["version"])
        if result != task["result"]:
            raise NativeMatterError("VERIFIED_RESULT_CHANGED")
        if task["status"] == "complete":
            return deepcopy(task["answer"])
        answer = {"task_id": task_id, "scenario_id": SCENARIO, "version": task["version"],
            "status": "DRAFT_PROOF_READY", "mode": SERVICE, "knowledge_published": False,
            "formal_business_answer": False, "human_review": "pending",
            "verification": task["verification"], "proofs": result["proofs"], "limits": result["limits"]}
        self._commit(task, "verified", {"status": "complete", "answer": answer})
        return deepcopy(answer)

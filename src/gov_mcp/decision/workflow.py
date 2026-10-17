"""Durable, synthetic end-to-end orchestration. No remote models or official sites.

The offline roles execute real tools, but their decisions are deterministic fixture
replays. This module deliberately cannot publish real policy or load provider keys.
"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
import threading
import uuid

from gov_mcp.classification import classify_corpus
from gov_mcp.knowledge.replay import SyntheticReplay
from gov_mcp.knowledge.store import Store, KBError, encoded
from .models import KnowledgeSnapshot, canonical_hash, reviewed_hash
from .service import DecisionService, DecisionError
from .adapters import OfflineRoles
from .connections import Connections, WiredRoles

ROOT = Path(__file__).resolve().parents[3]


class WorkflowError(ValueError):
    pass


def tokens(text):
    """Local sparse fallback: Chinese bigrams and Latin words, never dense embeddings."""
    return re.findall(r"[a-z0-9_-]+", text.lower()) + [text[i:i+2] for i in range(len(text)-1)
        if '\u4e00' <= text[i] <= '\u9fff' and '\u4e00' <= text[i+1] <= '\u9fff']


def vector(text):
    out = {}
    for token in tokens(text):
        out[token] = out.get(token, 0) + 1
    norm = math.sqrt(sum(v*v for v in out.values())) or 1
    return {k: v/norm for k, v in out.items()}


class Workflow:
    def __init__(self, root, *, clock=time.time, snapshot_path=None):
        self.store = Store(root, clock=clock)
        self.clock = clock
        self._service_cache = OrderedDict()
        self._search_cache = OrderedDict()
        self._cache_lock = threading.RLock()
        self.connections = Connections(ROOT / "config/decision/connections.json")
        self.roles = WiredRoles(OfflineRoles(ROOT / "config/decision/providers.template.json"), self.connections)
        self.classification_config = json.loads((ROOT / "config/preparation/classification.json").read_text("utf-8"))
        with self.store.db(True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS wf_records(kind TEXT,id TEXT,payload TEXT NOT NULL,PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS wf_versions(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS wf_record_status ON wf_records(kind,json_extract(payload,'$.status'))")
            if self.store.get("state", "wf_head", db) is None:
                snapshot = KnowledgeSnapshot.model_validate_json(Path(snapshot_path or ROOT / "config/decision/synthetic.snapshot.json").read_bytes())
                key = self._version(snapshot.model_dump(mode="json"), None, "fixture-bootstrap", db)
                self.store.put("state", "wf_head", key, db)
                self.store.put("state", "wf_documents", {"id": "initial", "assets": [a.model_dump() for a in snapshot.assets],
                    "evidence": [e.model_dump() for e in snapshot.evidence], "vectors": {}, "index_kind": "local_sparse_cosine"}, db)
                self.put("documents", "initial", self.store.get("state", "wf_documents", db), db)
                self.store.put("state", "wf_control", {"paused": True, "interval": 3600, "heartbeat_at": None}, db)
            # Pin legacy registry metadata once; rollback must not use a global registry.
            ontology_id = self.version(db=db)["snapshot"]["versions"]["ontology"]
            if self.get("ontology_version", ontology_id, db) is None:
                self.put("ontology_version", ontology_id, self.rows("ontology", db), db)

    def get(self, kind, key, db=None):
        if db is None:
            with self.store.db() as conn: return self.get(kind, key, conn)
        row = db.execute("SELECT payload FROM wf_records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, key, value, db):
        db.execute("INSERT INTO wf_records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (kind, key, encoded(value)))

    def rows(self, kind, db=None, *, limit=None, offset=0):
        if db is None:
            with self.store.db() as conn: return self.rows(kind, conn, limit=limit, offset=offset)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000): raise WorkflowError("INVALID_PAGE_LIMIT")
        if type(offset) is not int or not 0 <= offset <= 1000000: raise WorkflowError("INVALID_PAGE_OFFSET")
        if offset and limit is None: raise WorkflowError("OFFSET_REQUIRES_LIMIT")
        sql = "SELECT payload FROM wf_records WHERE kind=? ORDER BY rowid DESC"
        args = (kind,) if limit is None else (kind, limit, offset)
        return [json.loads(r[0]) for r in db.execute(sql if limit is None else sql+" LIMIT ? OFFSET ?", args)]

    def _count(self, kind, db):
        return db.execute("SELECT COUNT(*) FROM wf_records WHERE kind=?", (kind,)).fetchone()[0]

    def _packages(self, statuses, db=None, *, limit=10000):
        if db is None:
            with self.store.db() as conn: return self._packages(statuses, conn, limit=limit)
        if not 1 <= len(statuses) <= 5 or type(limit) is not int or not 1 <= limit <= 10000: raise WorkflowError("INVALID_PACKAGE_FILTER")
        placeholders = ",".join("?" for _ in statuses)
        sql = "SELECT payload FROM wf_records WHERE kind='package' AND json_extract(payload,'$.status') IN ("+placeholders+") ORDER BY rowid DESC LIMIT ?"
        return [json.loads(r[0]) for r in db.execute(sql, (*statuses, limit))]

    def _version(self, snapshot, base, actor, db):
        validated = KnowledgeSnapshot.model_validate_json(encoded(snapshot))
        key = validated.json_fingerprint()
        value = {"id": key, "base": base, "created_at": self.clock(), "actor": actor, "snapshot": snapshot}
        db.execute("INSERT OR IGNORE INTO wf_versions VALUES(?,?)", (key, encoded(value)))
        return key

    def version(self, version_id=None, db=None):
        if db is None:
            with self.store.db() as conn: return self.version(version_id, conn)
        key = version_id or self.store.get("state", "wf_head", db)
        row = db.execute("SELECT payload FROM wf_versions WHERE id=?", (key,)).fetchone()
        if not row: raise WorkflowError("VERSION_NOT_FOUND")
        value = json.loads(row[0])
        if canonical_hash(value["snapshot"]) != key: raise WorkflowError("VERSION_INTEGRITY_FAILURE")
        return value

    def _event(self, role, tool, inputs, outputs, *, task=None, run=None, db=None):
        if db is None:
            with self.store.db(True) as conn: return self._event(role, tool, inputs, outputs, task=task, run=run, db=conn)
        key = uuid.uuid4().hex
        item = {"id": key, "time": self.clock(), "role": role, "tool": tool, "task_id": task, "run_id": run,
                "adapter": "deterministic_offline_role_not_model", "model": None, "remote_calls": 0,
                "prepared_route": self.connections.config["roles"].get(role),
                "input_sha256": canonical_hash(inputs), "output_sha256": canonical_hash(outputs), "output": outputs}
        self.put("trace", key, item, db)
        return key

    def _service(self, version, db):
        # Cache only immutable validated structure, keyed by checked content, never answers.
        if canonical_hash(version["snapshot"]) != version["id"]: raise WorkflowError("VERSION_INTEGRITY_FAILURE")
        with self._cache_lock:
            cached = self._service_cache.get(version["id"])
            if cached is None:
                snapshot = KnowledgeSnapshot.model_validate_json(encoded(version["snapshot"]))
                template = DecisionService(snapshot)
                cached = (template, {r.id: reviewed_hash(r, snapshot) for r in snapshot.requirements})
                self._service_cache[version["id"]] = cached
                if len(self._service_cache) > 8: self._service_cache.popitem(last=False)
            self._service_cache.move_to_end(version["id"])
            service = cached[0].fork()
        hashes = cached[1]
        for block in self.rows("block", db):
            # Old snapshots remain blocked after a newer requirement is resolved.
            key = block["object_id"]
            if key in hashes and (not block.get("accepted_hash") or hashes[key] != block["accepted_hash"]):
                service.availability.suspend(key, change_id=block["package_id"], reason=block["reason"])
        service.availability.epoch = self._count("audit_gate", db)
        return service

    def branches(self, facts, parent=None):
        with self.store.db() as db:
            return self._service(self.version(db=db), db).branch_options(facts, parent)

    def query(self, facts, branch_id, *, parent_task=None, version_id=None, as_of=None, retriever=None, question=""):
        if not isinstance(question, str) or len(question) > 2000: raise WorkflowError("INVALID_QUESTION")
        # Snapshot and user facts are pinned once. The external question is data, not a tool plan.
        with self.store.db(True) as db:
            version = self.version(version_id, db)
            service = self._service(version, db)
            if parent_task and not self.get("query", parent_task, db): raise WorkflowError("PARENT_TASK_NOT_FOUND")
            task_id = service.prepare(facts, branch_id, as_of=date.fromisoformat(as_of) if as_of else None)
            draft = self.roles.invoke("query_controller", "plan_query", lambda: deepcopy(service._tasks[task_id]))
            task = {"id": task_id, "parent_task": parent_task, "version_id": version["id"], "facts": draft["facts"],
                    "branch_id": branch_id, "as_of": draft["as_of"], "question": question,
                    "created_at": self.clock(), "status": "retrieving", "rounds": [], "draft": draft, "answer": None}
            self.put("query", task_id, task, db)
            self._event("query_controller", "plan_query", {"branch_id": branch_id, "question": question},
                {"allowed_tools": ["retrieve_registered_evidence", "verify_conditions"], "max_rounds": 3}, task=task_id, db=db)
        try:
            return self._execute_query(task, version, draft, retriever)
        except Exception as error:
            with self.store.db(True) as db:
                task.update(status="failed", error_code=type(error).__name__, finished_at=self.clock())
                self.put("query", task_id, task, db)
                self.store.audit("workflow_query_failed", {"task_id": task_id, "code": type(error).__name__}, db)
            raise

    @staticmethod
    def _evidence_request(draft, found_ids=()):
        needed = {span["id"] for row in draft["rows"] for span in row["evidence"]}
        return sorted(needed - set(found_ids))

    def _verify_retrieval(self, draft, snapshot, retrieved, found_ids):
        assets = {asset["id"]: asset for asset in snapshot["assets"]}
        spans = {span["id"]: span for span in snapshot["evidence"]}
        requested = set(self._evidence_request(draft, found_ids)) & set(retrieved["requested"])
        valid = sorted({span["id"] for span in retrieved["evidence"] if span["id"] in requested and
            span == spans.get(span["id"]) and assets[span["asset_id"]]["text"][span["start"]:span["end"]] == span["quote"]})
        gaps = self._evidence_request(draft, set(found_ids) | set(valid))
        more = bool(gaps) and retrieved["round"] < 3
        return {"round": retrieved["round"], "requested": sorted(requested), "found": valid,
                "evidence_gaps": gaps, "needs_more": more, "status": "needs_retrieval" if more else "can_finalize",
                "unknown_user_fields": sorted({f for row in draft["rows"] for f in row["missing_fields"]}),
                "condition_results": [{"requirement_id": row["requirement_id"], "result": row["result"], "conditions": row["conditions"]}
                    for row in draft["rows"]]}

    def _execute_query(self, task, version, draft, retriever):
        task_id, branch_id = task["id"], task["branch_id"]
        found = set()
        evidence = {e["id"]: e for e in version["snapshot"]["evidence"]}
        for number in range(1, 4):
            requested = self._evidence_request(draft, found)
            if not requested:
                break
            result = self.roles.invoke("retriever", "retrieve_registered_evidence", lambda:
                retriever(number, list(requested), deepcopy(version["snapshot"])) if retriever else requested)
            retrieved = {"round": number, "requested": requested, "evidence": [evidence[key] for key in result if key in evidence]}
            record = self.roles.invoke("query_verifier", "verify_conditions", lambda:
                self._verify_retrieval(draft, version["snapshot"], retrieved, found))
            found.update(record["found"])
            with self.store.db(True) as db:
                task["rounds"].append(record)
                self.put("query", task_id, task, db)
                self._event("retriever", "retrieve_registered_evidence", {"round": number, "ids": requested}, {"ids": record["found"]}, task=task_id, db=db)
                self._event("query_verifier", "verify_conditions", {"evidence_ids": sorted(found)}, record, task=task_id, db=db)
            if not record["needs_more"]: break
        for row in draft["rows"]:
            if {e["id"] for e in row["evidence"]} - found:
                row.update(result="unknown", reason="EVIDENCE_RETRIEVAL_INCOMPLETE")
        task["draft"] = draft
        # The gate and durable answer are read/written in ONE serialized transaction.
        with self.store.db(True) as db:
            service = self._service(version, db)
            service._tasks[task_id] = draft
            answer = self.roles.invoke("query_controller", "compose_answer", lambda: service.finalize(task_id))
            answer.update(task_id=task_id, version_id=version["id"], retrieval_rounds=len(task["rounds"]),
                          execution_mode="offline_engineering_only", agent_model_calls=0, question_used_as_instruction=False)
            docs = self.store.get("state", "wf_documents", db)
            answer["documents_version"] = docs["id"]
            answer["candidate_evidence"] = [{"package_id": p["id"], "evidence": c["evidence"], "asset": c["asset"],
                "applicability": "待专业人员核实，不加入确定清单"} for p in self._packages(("awaiting_review", "partially_published"), db)
                for c in p["candidates"]
                if c["object_id"] not in p["published"] and c["record"]["branch_id"] in {b.id for b in service._path(branch_id)}]
            task.update(status="complete", answer=answer, finished_at=self.clock())
            self.put("query", task_id, task, db)
            self._event("query_controller", "compose_answer", {"rounds": len(task["rounds"])}, {"counts": answer["counts"]}, task=task_id, db=db)
        return answer

    def history(self, task_id):
        with self.store.db() as db:
            task = self.get("query", task_id, db)
            if not task: raise WorkflowError("QUERY_NOT_FOUND")
            if not task["answer"]: return {"task": task, "status": "failed_retry_as_new_task" if task["status"] == "failed" else "interrupted_retry_as_new_task"}
            service = self._service(self.version(task["version_id"], db), db)
            service._tasks[task_id] = task["draft"]
            return {"original_answer": task["answer"], "current_availability": service.finalize(task_id),
                    "rounds": task["rounds"], "parent_task": task["parent_task"], "version_id": task["version_id"]}

    def open_query(self, facts, branch_id, question=""):
        """Staged MCP entry: pin data once; a native controller can call children next."""
        if not isinstance(question, str) or len(question) > 2000: raise WorkflowError("INVALID_QUESTION")
        with self.store.db(True) as db:
            version = self.version(db=db)
            service = self._service(version, db)
            key = service.prepare(facts, branch_id)
            draft = deepcopy(service._tasks[key])
            task = {"id": key, "parent_task": None, "version_id": version["id"], "facts": draft["facts"],
                    "branch_id": branch_id, "as_of": draft["as_of"], "question": question, "created_at": self.clock(),
                    "status": "needs_retrieval", "rounds": [], "draft": draft, "answer": None,
                    "staged": True, "found_ids": [], "retrieval": None}
            if not self._evidence_request(draft):
                task["status"] = "can_finalize"
            self.put("query", key, task, db)
            self._event("query_controller", "open_query", {"branch_id": branch_id}, {"task_id": key, "version_id": version["id"]}, task=key, db=db)
            return {"task_id": key, "version_id": version["id"], "status": task["status"], "max_rounds": 3}

    def retrieve_query(self, task_id):
        with self.store.db(True) as db:
            task = self.get("query", task_id, db)
            if not task or not task.get("staged"): raise WorkflowError("STAGED_QUERY_REQUIRED")
            if task["status"] == "needs_verification": return deepcopy(task["retrieval"])  # Idempotent retry.
            if task["status"] != "needs_retrieval" or len(task["rounds"]) >= 3: raise WorkflowError("RETRIEVAL_NOT_ALLOWED")
            snapshot = self.version(task["version_id"], db)["snapshot"]
            requested = self._evidence_request(task["draft"], task["found_ids"])
            found = [e for e in snapshot["evidence"] if e["id"] in requested]
            # Native roles cannot choose arbitrary URLs or mutate the evidence set.
            value = {"task_id": task_id, "round": len(task["rounds"])+1, "requested": sorted(requested), "evidence": found,
                     "version_id": task["version_id"]}
            task.update(status="needs_verification", retrieval=value)
            self.put("query", task_id, task, db)
            self._event("retriever", "retrieve_query", {"task_id": task_id}, value, task=task_id, db=db)
            return value

    def verify_query(self, task_id):
        with self.store.db(True) as db:
            task = self.get("query", task_id, db)
            if not task or not task.get("staged"): raise WorkflowError("STAGED_QUERY_REQUIRED")
            if task["status"] in {"needs_retrieval", "can_finalize", "complete"} and task["rounds"]:
                return deepcopy(task["rounds"][-1])
            if task["status"] in {"can_finalize", "complete"}:
                snapshot = self.version(task["version_id"], db)["snapshot"]
                return {"task_id": task_id, **self._verify_retrieval(task["draft"], snapshot,
                    {"round": 0, "requested": [], "evidence": []}, [])}
            if task["status"] != "needs_verification": raise WorkflowError("VERIFICATION_NOT_ALLOWED")
            snapshot = self.version(task["version_id"], db)["snapshot"]
            retrieved = task["retrieval"]
            result = {"task_id": task_id, **self._verify_retrieval(task["draft"], snapshot, retrieved, task["found_ids"])}
            found = set(task["found_ids"]) | set(result["found"])
            task["rounds"].append(result)
            task.update(status=result["status"], found_ids=sorted(found), retrieval=None)
            self.put("query", task_id, task, db)
            self._event("query_verifier", "verify_query", {"task_id": task_id}, result, task=task_id, db=db)
            return result

    def finalize_query(self, task_id):
        with self.store.db(True) as db:
            task = self.get("query", task_id, db)
            if not task or not task.get("staged"): raise WorkflowError("STAGED_QUERY_REQUIRED")
            if task["status"] not in {"can_finalize", "complete"}: raise WorkflowError("QUERY_NOT_VALIDATED")
            version = self.version(task["version_id"], db)
            service = self._service(version, db)
            draft = deepcopy(task["draft"])
            for row in draft["rows"]:
                if {e["id"] for e in row["evidence"]}-set(task["found_ids"]):
                    row.update(result="unknown", reason="EVIDENCE_RETRIEVAL_INCOMPLETE")
            service._tasks[task_id] = draft
            answer = service.finalize(task_id)  # Rechecks live gate even for retries/old snapshots.
            answer.update(version_id=task["version_id"], retrieval_rounds=len(task["rounds"]),
                          execution_mode="staged_program_tools", agent_model_calls=None,
                          model_calls_scope="upstream Nexent inference is recorded separately; these tools do not infer")
            if task["answer"] is None:
                task.update(answer=answer, draft=draft, status="complete", finished_at=self.clock())
                self.put("query", task_id, task, db)
                self._event("query_controller", "finalize_query", {"task_id": task_id}, {"counts": answer["counts"]}, task=task_id, db=db)
            return answer

    def search_documents(self, text):
        if not isinstance(text, str) or len(text) > 2000: raise WorkflowError("INVALID_QUERY")
        docs = self.documents()
        q = vector(text)
        fingerprint = canonical_hash({"assets": docs["assets"], "evidence": docs["evidence"]})
        with self._cache_lock:
            index = self._search_cache.get(fingerprint)
            if index is None:
                spans = {e["id"]: e for e in docs["evidence"]}
                vectors, postings = {}, {}
                # Derive from verified text, never trust persisted vector hints.
                for key, span in spans.items():
                    vectors[key] = vector(span["quote"])
                    for term in vectors[key]: postings.setdefault(term, set()).add(key)
                index = (spans, vectors, postings)
                self._search_cache[fingerprint] = index
                if len(self._search_cache) > 2: self._search_cache.popitem(last=False)
            self._search_cache.move_to_end(fingerprint)
        spans, vectors, postings = index
        candidates = set().union(*(postings.get(term, set()) for term in q))
        rows = []
        for key in candidates:
            e, v = spans[key], vectors[key]
            score = sum(weight * v.get(k, 0) for k, weight in q.items())
            if score > 0:
                rows.append({**e, "score": round(score, 5), "business_rule_confirmed": False,
                             "note": "资料检索命中不代表已确认适用的办事要求"})
        return {"documents_version": docs["id"], "index_kind": "local_sparse_cosine", "hits": sorted(rows, key=lambda r: (-r["score"], r["id"]))[:20],
                "search_stats": {"total_spans": len(spans), "scored_spans": len(candidates), "algorithm": "verified_sparse_inverted_index"}}

    def documents(self, version_id=None):
        docs = self.get("documents", version_id) if version_id else self.store.get("state", "wf_documents")
        if not docs: raise WorkflowError("DOCUMENTS_VERSION_NOT_FOUND")
        if docs["id"] != "initial" and docs["id"] != canonical_hash({"assets": docs["assets"], "evidence": docs["evidence"]}):
            raise WorkflowError("DOCUMENTS_INTEGRITY_FAILURE")
        assets = {a["id"]: a for a in docs["assets"]}
        for a in assets.values():
            if hashlib.sha256(a["text"].encode()).hexdigest() != a["sha256"]: raise WorkflowError("DOCUMENTS_INTEGRITY_FAILURE")
        for e in docs["evidence"]:
            if e["asset_id"] not in assets or not 0 <= e["start"] < e["end"] <= len(assets[e["asset_id"]]["text"]) or assets[e["asset_id"]]["text"][e["start"]:e["end"]] != e["quote"]:
                raise WorkflowError("DOCUMENTS_INTEGRITY_FAILURE")
        return docs

    def configure_demo(self, *, actor):
        with self.store.db(True) as db:
            if not self.get("source", "demo-policy", db):
                source = {"id": "demo-policy", "url": "synthetic://gov-demo/update",
                    "profile": "baseline", "last_hash": None, "last_check": None, "next_check": 0, "status": "waiting",
                    "generation": 0, "enabled": True, "approved": True}
                source["last_hash"] = self._source_hash(source, self._packet(source))
                self.put("source", "demo-policy", source, db)
            self.store.audit("workflow_configure_synthetic_source", {"actor": actor}, db)

    def set_profile(self, profile, *, actor):
        if profile not in {"baseline", "changed", "unavailable", "noise"}: raise WorkflowError("UNKNOWN_DEMO_PROFILE")
        with self.store.db(True) as db:
            source = self.get("source", "demo-policy", db)
            if not source: raise WorkflowError("WAITING_SOURCE_CONFIGURATION")
            source.update(profile=profile, next_check=0)
            self.put("source", source["id"], source, db)
            self.store.audit("workflow_demo_profile", {"profile": profile, "actor": actor}, db)

    def control(self, *, paused, interval, actor):
        if type(paused) is not bool or type(interval) is not int or interval not in {60, 3600}: raise WorkflowError("INVALID_HEARTBEAT_CONFIG")
        with self.store.db(True) as db:
            old = self.store.get("state", "wf_control", db)
            old.update(paused=paused, interval=interval)
            self.store.put("state", "wf_control", old, db)
            self.store.audit("workflow_heartbeat_control", {"actor": actor, "paused": paused, "interval": interval}, db)

    def heartbeat(self, *, force=False):
        with self.store.db(True) as db:
            config = self.store.get("state", "wf_control", db)
            config["heartbeat_at"] = self.clock()
            self.store.put("state", "wf_control", config, db)
        if config["paused"] and not force: return {"status": "paused"}
        run = self.store.claim("workflow-"+uuid.uuid4().hex, 120, 600)
        if not run: return {"status": "already_running"}
        try:
            # A restart resumes the durable package stage; no missed-tick catchup.
            pending = self._packages(("processing",), limit=3)
            processed = 0
            for package in pending[:3]: processed += bool(self._process(package["id"], run))
            sources = [s for s in self.rows("source") if s["enabled"] and s["approved"] and (force or s["next_check"] <= self.clock())]
            for source in sources[:max(0, 3-processed)]: self._check(source, run, config["interval"])
            self.store.finish(run, "complete")
            return {"status": "complete", "run_id": run["id"]}
        except Exception as error:
            self.store.abort(run, "failed")
            self.store.put("state", "wf_last_error", {"run_id": run["id"], "code": type(error).__name__, "at": self.clock()})
            raise

    def _packet(self, source):
        if source["profile"] == "unavailable": raise WorkflowError("SIMULATED_SOURCE_UNAVAILABLE")
        packet = json.loads((ROOT / "config/decision/workflow-source.json").read_text("utf-8"))
        if source["profile"] in {"baseline", "noise"}:
            initial = json.loads((ROOT / "config/decision/synthetic.snapshot.json").read_text("utf-8"))
            spans = {e["id"]: e for e in initial["evidence"]}
            requirements = {r["id"]: r for r in initial["requirements"]}
            for c in packet["chunks"]:
                r = requirements[c["object_id"]]
                c.update(action=r["action"], quote=spans[r["evidence_ids"][0]]["quote"])
            packet["proposals"] = []
        return packet

    def _source_hash(self, source, packet):
        return canonical_hash({"source": source["id"], "body": packet, "config": "workflow-v1"})

    def _check(self, source, run, interval):
        try:
            packet = self._packet(source)
        except WorkflowError as error:
            with self.store.db(True) as db:
                self.store.assert_lease(run, db)
                latest = self.get("source", source["id"], db)
                if not latest or latest["profile"] != source["profile"]: return
                source.update(status="failed", error=str(error), last_check=self.clock(), next_check=self.clock()+interval)
                self.put("source", source["id"], source, db)
            return
        identity = self._source_hash(source, packet)
        with self.store.db(True) as db:
            self.store.assert_lease(run, db)
            latest = self.get("source", source["id"], db)
            if not latest or not latest["enabled"] or not latest["approved"] or latest["profile"] != source["profile"]:
                return  # A concurrent config edit must not be overwritten by an old check.
            source.update(last_check=self.clock(), next_check=self.clock()+interval, error=None)
            if source["last_hash"] == identity:
                source.update(status="unchanged", last_hash=identity)
                self.put("source", source["id"], source, db)
                return
            if packet["data_class"] != "synthetic" or len(packet["chunks"]) > 20: raise WorkflowError("PACKET_SCOPE_BLOCKED")
            base = self.version(db=db)
            source["generation"] = source.get("generation", 0)+1
            key = "change-"+canonical_hash({"fingerprint": identity, "generation": source["generation"]})[:24]
            for previous in self._packages(("processing", "awaiting_review", "partially_published"), db):
                if previous["source_id"] == source["id"] and previous["status"] not in {"published", "superseded"}:
                    previous.update(status="superseded", stage="superseded")
                    self.put("package", previous["id"], previous, db)
            candidates = self._extract_packet(packet, base["snapshot"], key)
            package = {"id": key, "source_id": source["id"], "fingerprint": identity, "base": base["id"],
                       "packet": packet, "candidates": candidates, "proposals": packet.get("proposals", []),
                       "created_at": self.clock(), "deadline": None, "status": "processing", "stage": "discovered",
                       "published": [], "reviews": {}, "run_id": run["id"], "classification": None}
            self.put("package", key, package, db)
            # As soon as a source requirement changes, it cannot remain a certain obligation.
            for candidate in candidates:
                block_id = key+":"+candidate["object_id"]
                block = {"id": block_id, "object_id": candidate["object_id"], "package_id": key,
                         "reason": "来源要求发生变化，等待专业人员确认", "accepted_hash": None}
                self.put("block", block_id, block, db)
                self.put("audit_gate", block_id, {"time": self.clock()}, db)
            source.update(status="changed", last_hash=identity, current_package=key)
            self.put("source", source["id"], source, db)
            self.put("seen", identity, {"package_id": key}, db)
            self.store.audit("workflow_change_discovered", {"package_id": key, "blocked": [c["object_id"] for c in candidates]}, db)
        self._process(key, run)

    def _extract_packet(self, packet, snapshot, package_id):
        """Replay fixture candidates; validate every field against the existing seed.

        Never a natural-language extractor. Production uses the existing native
        adapter, with review required before conversion to this rule schema.
        """
        existing = {r["id"]: r for r in snapshot["requirements"]}
        result, seen = [], set()
        for chunk in packet["chunks"]:
            if set(chunk) != {"object_id", "quote", "action"} or chunk["object_id"] not in existing or chunk["object_id"] in seen:
                raise WorkflowError("INVALID_CANDIDATE_TARGET")
            if not isinstance(chunk["quote"], str) or not 0 < len(chunk["quote"]) <= 4000 or chunk["action"] not in {"提交", "交验", "交回", "出示"}:
                raise WorkflowError("INVALID_CANDIDATE_CONTENT")
            if chunk["action"] not in chunk["quote"]: raise WorkflowError("ACTION_WITHOUT_QUOTE_SUPPORT")
            seen.add(chunk["object_id"])
            suffix = canonical_hash({"package": package_id, "chunk": chunk})[:18]
            asset_id, evidence_id = "asset-update-"+suffix, "ev-update-"+suffix
            text = "【工程模拟资料，不是真实政务政策】\n"+chunk["quote"]+"\n"
            start = text.index(chunk["quote"])
            asset = {"id": asset_id, "source_url": "synthetic://gov-demo/update", "title": "模拟更新-"+chunk["object_id"],
                     "revision": "rev-"+suffix, "data_class": "synthetic", "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
            evidence = {"id": evidence_id, "asset_id": asset_id, "start": start, "end": start+len(chunk["quote"]),
                        "quote": chunk["quote"], "locator": "模拟更新正文第2行"}
            record = {**existing[chunk["object_id"]], "action": chunk["action"], "evidence_ids": [evidence_id],
                      "review_status": "candidate", "review_id": None}
            result.append({"object_id": chunk["object_id"], "record": record, "asset": asset, "evidence": evidence,
                "conflict": chunk["action"] != existing[chunk["object_id"]]["action"],
                "status": "machine_candidate", "support_signal": "exact_fixture_quote_only_not_jev"})
        return result

    def _process(self, key, run):
        package = self.get("package", key)
        if package["status"] != "processing": return False
        self.store.renew(run, 120)
        with self.store.db(True) as db:
            self.store.assert_lease(run, db)
            try: self._source_guard(package, db)
            except WorkflowError as error:
                package["waiting_reason"] = str(error)
                self.put("package", key, package, db)
                return False  # A newer body can supersede this package in this same round.
            package["run_id"] = run["id"]
            package.pop("waiting_reason", None)
            self.put("package", key, package, db)
        if package["stage"] == "discovered":
            corpus = {"data_class": "synthetic", "assets": []}
            for c in package["candidates"]:
                a, e = c["asset"], c["evidence"]
                corpus["assets"].append({"asset_id": a["id"], "source_id": package["source_id"], "sha256": a["sha256"],
                    "title": a["title"], "modality": "text", "data_class": "synthetic", "segments": [
                        {"asset_id": a["id"], "segment_id": e["id"], "content": e["quote"], "locator": {"line": 2}}]})
            self.roles.invoke("update_controller", "plan_update", lambda: {"package_id": key})
            classification = self.roles.invoke("jev", "classify_changes", lambda: classify_corpus(corpus, self.classification_config, SyntheticReplay()))
            if classification["summary"]["status"] != "completed": raise WorkflowError("CLASSIFICATION_FAILED")
            classification["execution_mode"] = "synthetic_contract_replay_not_jev"
            classification["summary"].update(actual_models=[], replayed_contract_model=self.classification_config["model"])
            for document in classification["documents"]:
                for window in document["windows"]:
                    window.update(model=None, replayed_contract_model=self.classification_config["model"])
            for call in classification["calls"]:
                call.update(model=None, replayed_contract_model=self.classification_config["model"])
            for record in classification["records"]:
                record.update(model=None, replayed_contract_model=self.classification_config["model"])
            with self.store.db(True) as db:
                self.store.assert_lease(run, db)
                self._event("update_controller", "plan_update", {"package_id": key},
                    {"allowed_tools": ["classify_changes", "submit_candidates", "validate_candidates", "finish_run"]}, run=run["id"], db=db)
                package.update(classification=classification, stage="classified", run_id=run["id"])
                self.put("package", key, package, db)
                self._event("jev", "classify_changes", {"corpus": canonical_hash(corpus)},
                    {"provider": SyntheticReplay.name, "summary": classification["summary"]}, run=run["id"], db=db)
        if package["stage"] == "classified":
            # Shape validation is repeated at submission, not delegated to a model.
            candidates = self.roles.invoke("extractor", "submit_candidates", lambda:
                self._extract_packet(package["packet"], self.version(package["base"])["snapshot"], key))
            with self.store.db(True) as db:
                self.store.assert_lease(run, db)
                package.update(candidates=candidates, stage="extracted")
                self.put("package", key, package, db)
                self._event("extractor", "submit_candidates", {"package_id": key}, {"object_ids": [c["object_id"] for c in candidates]}, run=run["id"], db=db)
        if package["stage"] == "extracted":
            validation = self.roles.invoke("update_verifier", "validate_candidates", lambda: {"valid_structure": True, "exact_evidence": True,
                          "conflicts": [c["object_id"] for c in package["candidates"] if c["conflict"]], "requires_professional": True})
            self.roles.invoke("jev", "check_support", lambda: {"exact_fixture_quote_only": True})
            with self.store.db(True) as db:
                self.store.assert_lease(run, db)
                self._event("update_verifier", "validate_candidates", {"package_id": key}, validation, run=run["id"], db=db)
                self._event("jev", "check_support", {"package_id": key},
                    {"signal": "exact_fixture_quote_only_not_jev", "remote_calls": 0, "business_validity_proven": False}, run=run["id"], db=db)
                package.update(validation=validation, stage="validated")
                self.put("package", key, package, db)
        if package["stage"] == "validated":
            self.roles.invoke("update_controller", "finish_run", lambda: {"business_rules_published": False})
            with self.store.db(True) as db:
                self.store.assert_lease(run, db)
                self._source_guard(package, db)
                docs = self.store.get("state", "wf_documents", db)
                for c in package["candidates"]:
                    if c["asset"]["id"] not in {a["id"] for a in docs["assets"]}: docs["assets"].append(c["asset"])
                    if c["evidence"]["id"] not in {e["id"] for e in docs["evidence"]}: docs["evidence"].append(c["evidence"])
                    docs["vectors"][c["evidence"]["id"]] = vector(c["evidence"]["quote"])
                docs["id"] = canonical_hash({"assets": docs["assets"], "evidence": docs["evidence"]})
                self.put("documents", docs["id"], docs, db)
                self.store.put("state", "wf_documents", docs, db)
                package.update(stage="awaiting_review", status="awaiting_review")
                self.put("package", key, package, db)
                self._event("update_controller", "finish_run", {"package_id": key},
                    {"documents_version": docs["id"], "business_rules_published": False}, run=run["id"], db=db)
        return True

    def _source_guard(self, package, db):
        source = self.get("source", package["source_id"], db)
        if not source or not source["enabled"] or not source["approved"] or source.get("current_package") != package["id"]:
            raise WorkflowError("SOURCE_SUPERSEDED_OR_DISABLED")
        if self._source_hash(source, self._packet(source)) != package["fingerprint"]:
            raise WorkflowError("SOURCE_CONFIG_CHANGED")

    def deadline(self, package_id, deadline, *, actor):
        if deadline is not None:
            try:
                parsed = datetime.fromisoformat(deadline)
                if parsed.tzinfo is None: raise ValueError()
                deadline = parsed.timestamp()
            except (ValueError, TypeError): raise WorkflowError("DEADLINE_REQUIRES_TIMEZONE")
        with self.store.db(True) as db:
            package = self.get("package", package_id, db)
            if not package: raise WorkflowError("PACKAGE_NOT_FOUND")
            package["deadline"] = deadline
            self.put("package", package_id, package, db)
            self.store.audit("workflow_deadline_assigned", {"package_id": package_id, "deadline": deadline, "actor": actor}, db)

    def review(self, package_id, object_ids, *, actor, decision, note):
        if decision not in {"accepted", "rejected"} or not isinstance(note, str) or not 1 <= len(note) <= 2000:
            raise WorkflowError("INVALID_REVIEW")
        with self.store.db(True) as db:
            package = self.get("package", package_id, db)
            if not package or package["stage"] != "awaiting_review": raise WorkflowError("PACKAGE_NOT_REVIEWABLE")
            allowed = {c["object_id"] for c in package["candidates"]} | {p["id"] for p in package["proposals"]}
            if not object_ids or len(set(object_ids)) != len(object_ids) or not set(object_ids) <= allowed or set(object_ids) & set(package["published"]):
                raise WorkflowError("INVALID_REVIEW_SELECTION")
            for key in object_ids:
                item = next((c for c in package["candidates"] if c["object_id"] == key), None) or next(p for p in package["proposals"] if p["id"] == key)
                package["reviews"][key] = {"actor": actor, "decision": decision, "note": note, "at": self.clock(),
                                            "basis_hash": canonical_hash(item)}
            self.put("package", package_id, package, db)
            self.store.audit("workflow_professional_review", {"package_id": package_id, "ids": object_ids, "actor": actor, "decision": decision, "note": note}, db)

    def publish(self, package_id, object_ids, expected_head, *, actor):
        with self.store.db(True) as db:
            head = self.version(db=db)
            if head["id"] != expected_head: raise WorkflowError("BASE_VERSION_CHANGED")
            package = self.get("package", package_id, db)
            if not package or package["stage"] != "awaiting_review": raise WorkflowError("PACKAGE_NOT_REVIEWABLE")
            self._source_guard(package, db)
            if not object_ids or len(set(object_ids)) != len(object_ids) or set(object_ids) & set(package["published"]): raise WorkflowError("INVALID_PUBLICATION_SELECTION")
            candidates = {c["object_id"]: c for c in package["candidates"]}
            proposals = {p["id"]: p for p in package["proposals"]}
            data = deepcopy(head["snapshot"])
            base = self.version(package["base"], db)["snapshot"]
            ontology = deepcopy(self.get("ontology_version", data["versions"]["ontology"], db) or [])
            changed_records = []
            for key in object_ids:
                item = candidates.get(key) or proposals.get(key)
                r = package["reviews"].get(key)
                if not item or not r or r["decision"] != "accepted" or r["actor"] != actor or r["basis_hash"] != canonical_hash(item):
                    raise WorkflowError("ACCEPTED_REVIEW_BY_SAME_ACTOR_REQUIRED")
                if key in candidates:
                    # An unrelated publication may advance the base; changed dependencies may not.
                    old = next(v for v in base["requirements"] if v["id"] == key)
                    current = next(v for v in data["requirements"] if v["id"] == key)
                    if reviewed_hash(old, base) != reviewed_hash(current, data): raise WorkflowError("CANDIDATE_DEPENDENCIES_CHANGED")
                    c = candidates[key]
                    for field, value in (("assets", c["asset"]), ("evidence", c["evidence"])):
                        if value["id"] not in {v["id"] for v in data[field]}: data[field].append(value)
                    record = deepcopy(c["record"])
                    record.update(review_status="confirmed", review_id="review-published-"+uuid.uuid4().hex)
                    data["requirements"] = [record if v["id"] == key else v for v in data["requirements"]]
                    changed_records.append(record)
                else:
                    proposal = proposals[key]
                    if proposal["kind"] == "alias":
                        if proposal["material_id"] not in {v["id"] for v in data["materials"]} or not set(proposal["evidence_ids"]) <= {e["id"] for e in data["evidence"]}:
                            raise WorkflowError("PROPOSAL_DEPENDENCY_INCOMPLETE")
                        record = {"id": key, "material_id": proposal["material_id"], "label": proposal["label"],
                                  "evidence_ids": proposal["evidence_ids"], "review_status": "confirmed", "review_id": "review-published-"+uuid.uuid4().hex}
                        # Stable proposal IDs recur after source reversion. Replace atomically.
                        data["aliases"] = [v for v in data["aliases"] if v["id"] != key]+[record]
                        changed_records.append(record)
                    elif proposal["kind"] == "ontology":
                        # Registry extensions are documentation only until a parser/rule type supports them.
                        ontology = [v for v in ontology if v["id"] != key]+[{**proposal, "actor": actor, "confirmed_at": self.clock(), "executable": False}]
                        data["versions"]["ontology"] = "ontology-"+uuid.uuid4().hex
                    else: raise WorkflowError("UNSUPPORTED_PROPOSAL")
            # Set every chosen record's status before computing transitive review hashes.
            for record in changed_records:
                data["reviews"].append({"id": record["review_id"], "object_id": record["id"], "object_sha256": reviewed_hash(record, data),
                    "actor_id": actor, "actor_kind": "simulated_professional", "decision": "accepted", "note": "工程模拟专业人员确认；不代表真实业务审批。"})
            data["versions"].update(knowledge="knowledge-"+uuid.uuid4().hex, documents="documents-"+uuid.uuid4().hex)
            self._compact_snapshot(data)
            version_id = self._version(data, head["id"], actor, db)  # Full graph/review/span validation before switch.
            self.put("ontology_version", data["versions"]["ontology"], ontology, db)
            self.store.put("state", "wf_head", version_id, db)
            for c in changed_records:
                for block in self.rows("block", db):
                    if block["object_id"] == c["id"]:
                        block["accepted_hash"] = reviewed_hash(c, data)
                        self.put("block", block["id"], block, db)
                        self.put("audit_gate", uuid.uuid4().hex, {"time": self.clock()}, db)
            package["published"].extend(object_ids)
            package["status"] = "published" if set(package["published"]) == set(candidates) | set(proposals) else "partially_published"
            self.put("package", package_id, package, db)
            self.store.audit("workflow_professional_publish", {"package_id": package_id, "ids": object_ids, "actor": actor, "version": version_id, "base": head["id"]}, db)
            return {"version_id": version_id, "status": package["status"]}

    @staticmethod
    def _compact_snapshot(data):
        """Drop superseded unreferenced records from the active graph, not its history.

        Exact historical snapshots and source document versions remain immutable.
        Content hashes exclude unrelated records, so live review bindings stay valid.
        """
        records = [item for name in ("clauses", "conditions", "branches", "applicability", "requirements", "aliases") for item in data[name]]
        evidence_ids = {key for record in records for key in record["evidence_ids"]}
        review_ids = {record.get("review_id") for record in records}
        data["evidence"] = [e for e in data["evidence"] if e["id"] in evidence_ids]
        asset_ids = {e["asset_id"] for e in data["evidence"]}
        data["assets"] = [a for a in data["assets"] if a["id"] in asset_ids]
        data["reviews"] = [r for r in data["reviews"] if r["id"] in review_ids]

    def rollback(self, target, expected_head, *, actor):
        with self.store.db(True) as db:
            current = self.version(db=db)
            self.version(target, db)
            if current["id"] != expected_head: raise WorkflowError("BASE_VERSION_CHANGED")
            self.store.put("state", "wf_head", target, db)
            self.store.audit("workflow_professional_rollback", {"actor": actor, "from": expected_head, "to": target}, db)
            return {"version_id": target, "conflicts_automatically_cleared": False}

    def status(self, page=0):
        if type(page) is not int or not 0 <= page <= 10000: raise WorkflowError("INVALID_PAGE")
        with self.store.db() as db:
            packages = self.rows("package", db, limit=100, offset=page*100)
            for p in packages:
                p["overdue"] = p["deadline"] is not None and p["deadline"] < self.clock() and p["status"] not in {"published", "superseded"}
                p["deadline_state"] = "unassigned" if p["deadline"] is None else "overdue" if p["overdue"] else "assigned"
            versions = [{"id": r[0], "base": r[1], "actor": r[2], "created_at": r[3]} for r in db.execute(
                "SELECT id,json_extract(payload,'$.base'),json_extract(payload,'$.actor'),json_extract(payload,'$.created_at') FROM wf_versions ORDER BY rowid DESC LIMIT 100 OFFSET ?", (page*100,))]
            sources = self.rows("source", db)
            head = self.version(db=db)
            counts = {k: self._count(k, db) for k in ("package", "query", "trace")}
            counts["version"] = db.execute("SELECT COUNT(*) FROM wf_versions").fetchone()[0]
            limits = {"package": 100, "version": 100, "query": 50, "trace": 100}
            return {"mode": "synthetic_engineering_only", "real_policy_advice": False, "model_calls": 0,
                    "head": head["id"], "sources": sources,
                    "source_state": "configured" if sources else "waiting_source_configuration",
                    "control": self.store.get("state", "wf_control", db), "packages": packages,
                    "last_error": self.store.get("state", "wf_last_error", db),
                    "queries": [{k: q[k] for k in ("id", "created_at", "status", "version_id", "parent_task")} for q in self.rows("query", db, limit=50, offset=page*50)],
                    "versions": [{k: v[k] for k in ("id", "base", "actor", "created_at")} for v in versions],
                    "ontology": self.get("ontology_version", head["snapshot"]["versions"]["ontology"], db) or [], "traces": self.rows("trace", db, limit=100, offset=page*100),
                    "page_limits": {"packages": 100, "versions": 100, "queries": 50, "traces": 100},
                    "record_counts": counts, "page": page, "has_more": any(counts[k] > (page+1)*limit for k, limit in limits.items()),
                    "documents_version": self.store.get("state", "wf_documents", db)["id"]}

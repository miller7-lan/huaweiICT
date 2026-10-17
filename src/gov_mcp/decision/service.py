"""Offline branch/requirement decisions with exact evidence and a live suspension gate.

No Agent calls are simulated here. This is the deterministic foundation that
future query Agents will call, with snapshots isolated from existing runtimes.
"""
from __future__ import annotations

from copy import copy, deepcopy
from datetime import date
import json
from pathlib import Path
import threading
import uuid

from .logic import Truth, conjunction, evaluate
from .models import KnowledgeSnapshot

MAINTENANCE_MESSAGE = "工作人员正在更新相关政策，暂不提供服务"


class DecisionError(ValueError):
    pass


class AvailabilityGate:
    """Process-local current state, separate from historical knowledge snapshots.

    There is intentionally no public resume or publication operation in phase 1.
    A later authenticated review/publication service must own recovery.
    """
    def __init__(self, known_ids):
        self.lock = threading.RLock()
        self.known_ids = frozenset(known_ids)
        self.epoch = 0
        self.suspended = {}

    def suspend(self, object_id, *, change_id, reason):
        if object_id not in self.known_ids:
            raise DecisionError("UNKNOWN_SUSPENSION_OBJECT")
        if not isinstance(change_id, str) or not change_id.strip() or len(change_id) > 80:
            raise DecisionError("INVALID_CHANGE_ID")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise DecisionError("INVALID_SUSPENSION_REASON")
        with self.lock:
            self.suspended[object_id] = {"object_id": object_id, "change_id": change_id, "reason": reason}
            self.epoch += 1


class DecisionService:
    def __init__(self, snapshot: KnowledgeSnapshot):
        # Revalidate a fresh copy: frozen Pydantic models can still contain mutable containers.
        self._snapshot = KnowledgeSnapshot.model_validate_json(snapshot.model_dump_json())
        bundle = self._snapshot
        self._fingerprint = bundle.json_fingerprint()
        self._fields = {f.id: f.model_dump() for f in bundle.fields}
        self._conditions = {c.id: c for c in bundle.conditions}
        self._branches = {b.id: b for b in bundle.branches}
        self._scopes = {s.id: s for s in bundle.applicability}
        self._assets = {a.id: a for a in bundle.assets}
        self._evidence = {e.id: e for e in bundle.evidence}
        self._clauses = {c.id: c for c in bundle.clauses}
        self._materials = {m.id: m for m in bundle.materials}
        self._stages = {s.id: s for s in bundle.stages}
        known_ids = {bundle.matter.id}
        for name in ("conditions", "branches", "applicability", "assets", "evidence", "clauses", "materials", "stages", "requirements", "aliases"):
            known_ids.update(record.id for record in getattr(bundle, name))
        self.availability = AvailabilityGate(known_ids)
        self._tasks = {}
        self._task_lock = threading.RLock()

    def fork(self):
        """Reuse private validated indexes; never share tasks or live availability state.

        Snapshot/index objects are read-only inside this service. All public answers
        copy their nested values. A fork must reconstruct its current gate from storage.
        """
        result = copy(self)
        result.availability = AvailabilityGate(self.availability.known_ids)
        result._tasks = {}
        result._task_lock = threading.RLock()
        return result

    @classmethod
    def from_file(cls, path):
        path = Path(path)
        if path.stat().st_size > 5 * 1024**2:
            raise DecisionError("SNAPSHOT_TOO_LARGE")
        return cls(KnowledgeSnapshot.model_validate_json(path.read_bytes()))

    def _facts(self, facts):
        if not isinstance(facts, dict) or len(facts) > 64 or any(key not in self._fields for key in facts):
            raise DecisionError("UNREGISTERED_FACTS")
        # Invalid values for a known field remain UNKNOWN in the rule engine.
        # Limit shapes first so arbitrary nested objects do not enter trace serialization.
        if any(value is not None and type(value) not in (str, int, float, bool) for value in facts.values()):
            raise DecisionError("INVALID_FACT_SHAPE")
        if any(isinstance(value, str) and len(value) > 1024 for value in facts.values()):
            raise DecisionError("FACT_TOO_LARGE")
        return deepcopy(facts)

    def _record_dependencies(self, records):
        ids = {self._snapshot.matter.id}
        for record in records:
            ids.add(record.id)
            for ref in record.evidence_ids:
                ids.update((ref, self._evidence[ref].asset_id))
        return ids

    def _path(self, branch_id):
        if branch_id not in self._branches:
            raise DecisionError("UNKNOWN_BRANCH")
        current, path = self._branches[branch_id], []
        while current:
            path.append(current)
            current = self._branches.get(current.parent_id)
        return list(reversed(path))

    def branch_options(self, facts, parent_id=None):
        facts = self._facts(facts)
        cache = {}
        if parent_id is not None:
            path = self._path(parent_id)
            states = [self._condition(b.condition_id, facts, cache)["result"] for b in path]
            if conjunction([Truth(v) for v in states]) is Truth.FALSE:
                return []
        result = []
        with self.availability.lock:
            for branch in sorted(self._snapshot.branches, key=lambda b: (b.order, b.id)):
                if branch.parent_id != parent_id or branch.review_status != "confirmed":
                    continue
                path = self._path(branch.id)
                dependencies = self._record_dependencies([*path, *(self._conditions[b.condition_id] for b in path)])
                if dependencies & self.availability.suspended.keys():
                    continue
                conditions = [self._condition(b.condition_id, facts, cache) for b in path]
                state = conjunction([Truth(c["result"]) for c in conditions])
                if state is not Truth.FALSE:
                    result.append({"id": branch.id, "label": branch.label, "condition_result": state.value,
                                   "missing_fields": sorted({f for c in conditions for f in c["missing_fields"]})})
        return result

    def _condition(self, condition_id, facts, cache=None):
        if cache is not None and condition_id in cache: return deepcopy(cache[condition_id])
        condition = self._conditions[condition_id]
        if condition.review_status != "confirmed":
            return {"condition_id": condition_id, "result": "unknown", "reason": "CONDITION_NOT_CONFIRMED", "missing_fields": []}
        value = evaluate(condition.expression, facts, self._fields)
        result = {"condition_id": condition_id, "result": value.result.value, "trace": value.trace,
                  "missing_fields": list(value.missing_fields), "issues": list(value.issues)}
        if cache is not None: cache[condition_id] = result
        return deepcopy(result)

    def _scope(self, scope, region_id, as_of):
        if scope.review_status != "confirmed":
            value, reason = Truth.UNKNOWN, "APPLICABILITY_NOT_CONFIRMED"
        elif scope.region_id != region_id:
            value, reason = Truth.FALSE, "REGION_NOT_APPLICABLE"
        elif not scope.window.confirmed:
            value, reason = Truth.UNKNOWN, "TIME_APPLICABILITY_UNKNOWN"
        elif scope.window.start_inclusive and as_of < scope.window.start_inclusive:
            value, reason = Truth.FALSE, "NOT_EFFECTIVE_YET"
        elif scope.window.end_exclusive and as_of >= scope.window.end_exclusive:
            value, reason = Truth.FALSE, "OUTSIDE_EFFECTIVE_WINDOW"
        else:
            value, reason = Truth.TRUE, "CONFIRMED_APPLICABILITY"
        return {"id": scope.id, "result": value.value, "reason": reason,
                "region_id": region_id, "as_of": as_of.isoformat()}

    def _citations(self, ids):
        result = []
        for evidence_id in sorted(set(ids)):
            span = self._evidence[evidence_id]
            asset = self._assets[span.asset_id]
            result.append({**span.model_dump(), "source_url": asset.source_url, "asset_sha256": asset.sha256,
                           "asset_revision": asset.revision, "asset_title": asset.title,
                           "documents_version": self._snapshot.versions.documents,
                           "range_unit": "unicode_codepoint", "range_convention": "start_inclusive_end_exclusive"})
        return result

    def _requirement(self, requirement, facts, region_id, as_of, cache=None):
        branches = self._path(requirement.branch_id)
        conditions = [self._condition(b.condition_id, facts, cache) for b in branches]
        conditions.append(self._condition(requirement.condition_id, facts, cache))
        scope = self._scopes[requirement.applicability_id]
        applicability = self._scope(scope, region_id, as_of)
        if requirement.review_status != "confirmed":
            state, reason = Truth.UNKNOWN, "REQUIREMENT_NOT_CONFIRMED"
        else:
            state = conjunction([Truth(c["result"]) for c in conditions] + [Truth(applicability["result"])])
            reason = "STRUCTURED_RULE_EVALUATED"
        evidence = set(requirement.evidence_ids) | set(scope.evidence_ids)
        dependencies = {self._snapshot.matter.id, requirement.id, requirement.material_id, requirement.stage_id, scope.id}
        path = []
        parent = self._snapshot.matter.id
        for branch in branches:
            evidence.update(branch.evidence_ids)
            dependencies.update([branch.id, branch.condition_id])
            path.append({"from": parent, "relation": "has_branch", "to": branch.id, "evidence_ids": branch.evidence_ids})
            parent = branch.id
        path.append({"from": parent, "relation": "has_requirement", "to": requirement.id, "evidence_ids": requirement.evidence_ids})
        path.extend([
            {"from": requirement.id, "relation": "uses_material", "to": requirement.material_id, "evidence_ids": requirement.evidence_ids},
            {"from": requirement.id, "relation": "in_stage", "to": requirement.stage_id, "evidence_ids": requirement.evidence_ids},
            {"from": requirement.id, "relation": "applies_when", "to": requirement.condition_id, "evidence_ids": self._conditions[requirement.condition_id].evidence_ids},
            {"from": requirement.id, "relation": "has_applicability", "to": scope.id, "evidence_ids": scope.evidence_ids},
        ])
        for item in conditions:
            rule = self._conditions[item["condition_id"]]
            evidence.update(rule.evidence_ids)
            dependencies.add(rule.id)
        for clause_id in scope.clause_ids:
            clause = self._clauses[clause_id]
            evidence.update(clause.evidence_ids)
            dependencies.add(clause_id)
            path.append({"from": scope.id, "relation": "aligned_to", "to": clause_id, "evidence_ids": scope.evidence_ids})
        dependencies.update(evidence)
        dependencies.update(self._evidence[ref].asset_id for ref in evidence)
        missing = sorted({field for item in conditions for field in item["missing_fields"]})
        return {"requirement_id": requirement.id, "material_id": requirement.material_id,
                "material": self._materials[requirement.material_id].label, "action": requirement.action,
                "stage_id": requirement.stage_id, "branch_id": requirement.branch_id,
                "result": state.value, "reason": reason, "review_status": requirement.review_status,
                "conditions": conditions, "applicability": applicability, "missing_fields": missing,
                "dependency_ids": sorted(dependencies), "evidence": self._citations(evidence), "path": path}

    def prepare(self, facts, branch_id, *, region_id=None, as_of=None):
        """Prepare a server-owned draft. A selected ID does not invent missing user facts."""
        facts = self._facts(facts)
        branches = self._path(branch_id)
        if any(branch.review_status != "confirmed" for branch in branches):
            raise DecisionError("BRANCH_NOT_CONFIRMED")
        as_of = self._snapshot.demo_date if as_of is None else as_of
        if type(as_of) is not date:
            raise DecisionError("AS_OF_REQUIRES_DATE")
        region_id = self._snapshot.matter.region_id if region_id is None else region_id
        cache = {}
        branch_results = [self._condition(b.condition_id, facts, cache) for b in branches]
        branch_state = conjunction([Truth(item["result"]) for item in branch_results])
        selected_ids = {b.id for b in branches}
        rows = [self._requirement(r, facts, region_id, as_of, cache) for r in self._snapshot.requirements if r.branch_id in selected_ids]
        draft = {"facts": facts, "branch_id": branch_id, "branch_result": branch_state.value,
                 "branch_conditions": branch_results, "region_id": region_id, "as_of": as_of.isoformat(), "rows": rows}
        task_id = uuid.uuid4().hex
        with self._task_lock:
            if len(self._tasks) >= 64:
                raise DecisionError("OFFLINE_TASK_LIMIT")
            self._tasks[task_id] = deepcopy(draft)
        return task_id

    def finalize(self, task_id):
        with self._task_lock:
            if task_id not in self._tasks:
                raise DecisionError("UNKNOWN_TASK")
            draft = deepcopy(self._tasks[task_id])
        # Re-read current suspensions even when the task uses an older fixed snapshot.
        with self.availability.lock:
            checklist, pending, excluded, paused = [], [], [], []
            for row in draft["rows"]:
                blockers = sorted(set(row["dependency_ids"]) & self.availability.suspended.keys())
                if blockers:
                    row.update(result="paused", message=MAINTENANCE_MESSAGE,
                               suspension=[deepcopy(self.availability.suspended[key]) for key in blockers])
                    paused.append(row)
                elif draft["branch_result"] == "false":
                    if row["review_status"] == "confirmed":
                        row.update(result="false", reason="SELECTED_BRANCH_NOT_APPLICABLE")
                        excluded.append(row)
                elif row["result"] == "true":
                    checklist.append(row)
                elif row["result"] == "false":
                    excluded.append(row)
                else:
                    row["applicability_note"] = "原文事实仅供核对，尚不能确定为本次办事要求。"
                    pending.append(row)
            stages = [{"stage_id": s.id, "label": s.label,
                       "items": [row for row in checklist if row["stage_id"] == s.id]}
                      for s in sorted(self._snapshot.stages, key=lambda s: (s.order, s.id))]
            missing = sorted({field for row in pending for field in row["missing_fields"]})
            if not draft["rows"] and draft["branch_result"] == "unknown":
                missing = sorted({field for c in draft["branch_conditions"] for field in c["missing_fields"]})
            aliases = [{"material_id": alias.material_id, "label": alias.label, "evidence_ids": alias.evidence_ids}
                       for alias in self._snapshot.aliases
                       if alias.review_status == "confirmed" and not
                       (self._record_dependencies([alias]) | {alias.material_id}) & self.availability.suspended.keys()]
            next_branches = self.branch_options(draft["facts"], draft["branch_id"])
            status = ("branch_not_applicable" if draft["branch_result"] == "false" else
                      "needs_branch_selection" if not draft["rows"] and next_branches else
                      "no_confirmed_requirements" if not draft["rows"] else
                      "partial" if checklist and (pending or paused) else "ready" if checklist else
                      "paused" if paused else "insufficient_evidence")
            result = {"mode": self._snapshot.mode, "real_policy_advice": False, "model_calls": 0,
                      "task_id": task_id, "snapshot_sha256": self._fingerprint,
                      "versions": self._snapshot.versions.model_dump(), "availability_epoch": self.availability.epoch,
                      "matter": self._snapshot.matter.model_dump(), "branch_id": draft["branch_id"],
                      "status": status, "next_branches": next_branches,
                      "branch_result": draft["branch_result"], "region_id": draft["region_id"], "as_of": draft["as_of"],
                      "stages": stages, "pending": pending, "excluded": excluded, "paused": paused,
                      "confirmed_aliases": aliases,
                      "follow_up": {"optional": True, "enabled": bool(missing), "label": "补充情况", "fields": [self._fields[f] for f in missing]},
                      "counts": {"confirmed": len(checklist), "pending": len(pending), "excluded": len(excluded), "paused": len(paused)}}
            # Return JSON-safe values and never expose references to internal snapshot/task data.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            return deepcopy(result)

    def answer(self, facts, branch_id, *, region_id=None, as_of=None):
        return self.finalize(self.prepare(facts, branch_id, region_id=region_id, as_of=as_of))

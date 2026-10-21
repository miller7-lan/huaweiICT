"""Reviewed ontology execution in an isolated workspace, never business publication."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import time

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge.store import Store, digest
from gov_mcp.knowledge_landing import retrieve


FORMAT = "ontology-engineering-v1"
FIELDS = {"local_household", "age_years", "card_years", "photo_years", "fingerprint_count"}


class PublicOntologySource:
    """Read saved public sources. This performs no remote freshness check."""
    def __init__(self, root):
        self.root = Path(root)
        self.store = KnowledgeStore(self.root, read_only=True)

    def _state(self, source_id):
        with self.store.db() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM sites WHERE id=?", (source_id,)).fetchone()
            if row is None or row["failures"] or row["paused_reason"]:
                raise ValueError("SOURCE_PAUSED_OR_FAILED")
            state = dict(row)
            state["config"] = json.loads(state["config"])
            if state["config"].get("enabled") is not True:
                raise ValueError("SOURCE_DISABLED")
            state["revisions"] = [dict(item) for item in db.execute(
                "SELECT r.* FROM revisions r JOIN documents d ON r.document_id=d.id WHERE d.site_id=? ORDER BY r.rowid", (source_id,))]
            return state

    def __call__(self):
        from gov_mcp.ontology_seed import build_candidate
        raw_snapshot = (self.root / "snapshot.json").read_bytes()
        snapshot = json.loads(raw_snapshot)
        captured = datetime.fromisoformat(snapshot["captured_at"])
        if captured.tzinfo is None or not 0 <= time.time() - captured.timestamp() <= 86400:
            raise ValueError("ARCHIVED_SNAPSHOT_EXPIRED_REFRESH_REQUIRES_SEPARATE_ACTION")
        source_id = "BJ-ID-RENEWAL-2025"
        acquisitions = [r for r in snapshot["acquisitions"] if r["source_id"] == source_id]
        if (len(acquisitions) != 1 or acquisitions[0].get("http_status") != 200
                or acquisitions[0].get("all_selected_quotes_match") is not True):
            raise ValueError("SUCCESSFUL_ARCHIVE_RECEIPT_REQUIRED")
        before = self._state(source_id)
        evidence = retrieve(self.store, snapshot["bindings"], "损坏换领网上办理条件", "北京市", "resident-id-renewal")
        proposal = build_candidate(evidence)
        after = self._state(source_id)
        if before != after or (self.root / "snapshot.json").read_bytes() != raw_snapshot:
            raise ValueError("SOURCE_CHANGED_DURING_READ")
        return {"candidate": proposal, "binding": digest({"snapshot_sha256": hashlib.sha256(raw_snapshot).hexdigest(),
                "site_state": after, "evidence": evidence}),
                "freshness_basis": "successful_archived_snapshot_within_24h_not_live_recheck"}


class OntologyWorkspace:
    @staticmethod
    def _implementation():
        return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path(__file__), Path(__file__).with_name("ontology_seed.py"))})

    def __init__(self, root, source_reader):
        self.root = Path(root)
        marker = self.root / "ontology-workspace.json"
        if not marker.is_file() or json.loads(marker.read_text("utf-8")).get("format") != FORMAT:
            raise ValueError("ISOLATED_ONTOLOGY_WORKSPACE_REQUIRED")
        self.store, self.source_reader = Store(self.root), source_reader

    @classmethod
    def initialize(cls, root, seed, source_reader):
        root = Path(root)
        marker = root / "ontology-workspace.json"
        if root.exists() and not marker.exists() and any(root.iterdir()):
            raise ValueError("ISOLATED_ONTOLOGY_WORKSPACE_REQUIRED")
        if not root.exists():
            root.mkdir(parents=True)
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as stream:
                json.dump({"format": FORMAT, "seed_sha256": digest(seed)}, stream)
        saved = json.loads(marker.read_text("utf-8"))
        if saved != {"format": FORMAT, "seed_sha256": digest(seed)}:
            raise ValueError("WORKSPACE_SEED_CHANGED")
        workspace = cls(root, source_reader)
        with workspace.store.db(True) as db:
            if workspace.store.get("state", "active_rules", db) is None:
                workspace.store.put("state", "ontology_implementation", workspace._implementation(), db)
                workspace.store.publish("rules", {"format": FORMAT, "seed": deepcopy(seed), "instances": []},
                                        None, "system:engineering-seed", db=db)
        return workspace

    @staticmethod
    def _actor(actor, confirmed):
        if (confirmed is not True or not isinstance(actor, dict) or set(actor) != {"kind", "name"}
                or actor["kind"] != "human" or not isinstance(actor["name"], str)
                or not 1 <= len(actor["name"].strip()) <= 100):
            raise ValueError("EXPLICIT_HUMAN_STRUCTURAL_CONFIRMATION_REQUIRED")
        return deepcopy(actor)

    @staticmethod
    def _proposal(value):
        if (type(value.get("schema_version")) is not int or value.get("schema_version") != 1 or value.get("data_class") != "public"
                or value.get("status") != "pending_review"
                or value.get("formally_published") is not False or value.get("formal_approval") is not False
                or value.get("business_conclusion_status") != "PAUSED_UNREVIEWED"
                or value.get("current_applicability") != "unknown"
                or value.get("entity_type") != {"name": "ServiceChannel", "label": "办理渠道"}
                or value.get("relation_type") != {"name": "available_via", "domain": "Matter", "range": "ServiceChannel", "operator": "all"}
                or value.get("channel") != "online" or value.get("scope", {}).get("branch") != "damaged"):
            raise ValueError("UNSUPPORTED_ONTOLOGY_PROPOSAL")
        conditions = value.get("conditions", [])
        if len(conditions) != 5 or {c.get("field") for c in conditions} != FIELDS:
            raise ValueError("INCOMPLETE_CONJUNCTION")
        expected = {"local_household": "eq", "age_years": "gte", "card_years": "lte", "photo_years": "lte", "fingerprint_count": "gte"}
        for condition in conditions:
            field = condition["field"]
            threshold = condition.get("value")
            if set(condition) != {"field", "op", "value"} or condition["op"] != expected[field]:
                raise ValueError("UNSUPPORTED_CONDITION")
            if field == "local_household":
                if threshold is not True: raise ValueError("UNSUPPORTED_CONDITION")
            elif type(threshold) is not int or threshold < 1:
                raise ValueError("UNSUPPORTED_CONDITION")
        if set(value.get("ambiguous_boundaries", [])) != {"age_years", "card_years", "photo_years"}:
            raise ValueError("BOUNDARY_REVIEW_REQUIRED")
        citations = value.get("citations", [])
        if len(citations) != 2:
            raise ValueError("BOTH_SOURCE_CLAUSES_REQUIRED")
        for citation in citations:
            if hashlib.sha256(citation["quote"].encode()).hexdigest() != citation["text_sha256"]:
                raise ValueError("QUOTE_INTEGRITY_FAILURE")
        return value

    def _current(self):
        current = self.source_reader()
        self._proposal(current["candidate"])
        if (not isinstance(current.get("binding"), str) or len(current["binding"]) != 64
                or any(c not in "0123456789abcdef" for c in current["binding"])):
            raise ValueError("SOURCE_BINDING_REQUIRED")
        return deepcopy(current)

    def _version(self, head):
        version = self.store.version(head)
        if version["kind"] != "rules" or version["data"].get("format") != FORMAT:
            raise ValueError("UNRELATED_ONTOLOGY_VERSION")
        seed = version["data"]["seed"]
        kinds = [item["name"] for item in seed["entity_types"]]
        relations = [item["name"] for item in seed["relation_types"]]
        if len(kinds) != len(set(kinds)) or len(relations) != len(set(relations)):
            raise ValueError("DUPLICATE_ONTOLOGY_DEFINITION")
        if any(item["domain"] not in kinds or item["range"] not in kinds for item in seed["relation_types"]):
            raise ValueError("ONTOLOGY_ENDPOINT_TYPE_MISMATCH")
        implementation = self.store.get("state", "ontology_implementation")
        if implementation != self._implementation():
            raise ValueError("ONTOLOGY_IMPLEMENTATION_CHANGED_REVIEW_MIGRATION_REQUIRED")
        return version

    def submit(self):
        current = self._current()
        proposal, binding = current["candidate"], current["binding"]
        identifier = digest({"proposal": proposal, "source_binding": binding})
        with self.store.db(True) as db:
            existing = self.store.get("candidates", identifier, db)
            if existing is None:
                self.store.put("candidates", identifier, {"id": identifier, "proposal": proposal, "source_binding": binding,
                    "status": "pending_review", "created_at": self.store.clock()}, db)
                self.store.audit("ontology_candidate", {"candidate_id": identifier, "source_binding": binding}, db)
        return identifier

    def status(self):
        return {"head": self.store.get("state", "active_rules"), "candidates": self.store.rows("candidates"),
                "mode": FORMAT, "formally_published": False, "business_conclusion_status": "PAUSED_UNREVIEWED"}

    def review(self, candidate_id, expected_head, candidate_sha256, actor, *, confirmed=False):
        actor = self._actor(actor, confirmed)
        current = self._current()
        with self.store.db(True) as db:
            head = self.store.get("state", "active_rules", db)
            if head != expected_head: raise ValueError("BASE_VERSION_CHANGED")
            old = self._version(head)
            record = self.store.get("candidates", candidate_id, db)
            if not record or record["status"] != "pending_review": raise ValueError("CANDIDATE_NOT_PENDING")
            actual = digest({"proposal": record["proposal"], "source_binding": record["source_binding"]})
            if actual != candidate_id or candidate_sha256 != candidate_id:
                raise ValueError("CANDIDATE_DIGEST_CHANGED")
            if record["source_binding"] != current["binding"] or record["proposal"] != current["candidate"]:
                raise ValueError("SOURCE_CHANGED_SINCE_PROPOSAL")
            data = deepcopy(old["data"])
            proposal = record["proposal"]
            for group, new in (("entity_types", proposal["entity_type"]), ("relation_types", proposal["relation_type"])):
                previous = [item for item in data["seed"][group] if item["name"] == new["name"]]
                if previous and previous != [new]: raise ValueError("INCOMPATIBLE_ONTOLOGY_CHANGE")
                if not previous: data["seed"][group].append(new)
            instance = {"candidate_id": candidate_id, "proposal": proposal, "source_binding": record["source_binding"],
                        "review": {"actor": actor, "confirmed_at": self.store.clock(), "scope": "structure_only_not_business_approval"}}
            data["instances"] = [instance]
            if self._current() != current: raise ValueError("SOURCE_CHANGED_DURING_REVIEW")
            version = self.store.publish("rules", data, expected_head, actor, db=db)
            record.update(status="accepted_structure_only", version=version, review=instance["review"])
            self.store.put("candidates", candidate_id, record, db)
            return version

    def _guard_version(self, version, current):
        for instance in version["data"]["instances"]:
            proposal = self._proposal(instance["proposal"])
            seed = version["data"]["seed"]
            if ([item for item in seed["entity_types"] if item["name"] == proposal["entity_type"]["name"]] != [proposal["entity_type"]]
                    or [item for item in seed["relation_types"] if item["name"] == proposal["relation_type"]["name"]] != [proposal["relation_type"]]):
                raise ValueError("EXECUTABLE_RELATION_NOT_REGISTERED")
            if (instance["source_binding"] != current["binding"] or proposal != current["candidate"]
                    or instance["candidate_id"] != digest({"proposal": proposal, "source_binding": instance["source_binding"]})
                    or instance.get("review", {}).get("actor", {}).get("kind") != "human"):
                raise ValueError("ACTIVE_ONTOLOGY_SOURCE_OR_REVIEW_CHANGED")

    @staticmethod
    def _facts(facts):
        if not isinstance(facts, dict) or set(facts) - FIELDS:
            raise ValueError("UNREGISTERED_OR_PRIVATE_FACT")
        for field, value in facts.items():
            if value is None: continue
            if field == "local_household":
                if type(value) is not bool: raise ValueError("INVALID_CONDITION_FACT")
            elif field == "fingerprint_count" and type(value) is not int:
                raise ValueError("INVALID_CONDITION_FACT")
            elif type(value) not in (int, float) or not 0 <= value <= 1000 or not math.isfinite(value):
                raise ValueError("INVALID_CONDITION_FACT")

    def query(self, region, matter, branch, channel, facts):
        self._facts(facts)
        head = self.store.get("state", "active_rules")
        version = self._version(head)
        result = {"status": "UNSUPPORTED_RELATION", "source_conditions_result": "unknown", "version": head,
                  "formally_published": False, "formal_approval": False,
                  "business_conclusion_status": "PAUSED_UNREVIEWED", "current_applicability": "unknown",
                  "trace": [], "citations": []}
        if not version["data"]["instances"]:
            if self.store.get("state", "active_rules") != head:
                raise ValueError("ONTOLOGY_CHANGED_DURING_QUERY")
            return result
        current = self._current()
        self._guard_version(version, current)
        proposal = version["data"]["instances"][0]["proposal"]
        scope = proposal["scope"]
        if (region, matter, branch, channel) != (scope["region"], scope["matter"], scope["branch"], proposal["channel"]):
            result["status"] = "OUT_OF_SCOPE"
        else:
            trace = []
            for condition in proposal["conditions"]:
                field, threshold = condition["field"], condition["value"]
                value = facts.get(field)
                if value is None:
                    verdict, reason = "unknown", "missing_fact"
                elif field in proposal["ambiguous_boundaries"] and value == threshold:
                    verdict, reason = "unknown", "literal_boundary_needs_semantic_review"
                else:
                    matched = value == threshold if condition["op"] == "eq" else value >= threshold if condition["op"] == "gte" else value <= threshold
                    verdict, reason = ("true" if matched else "false"), "source_condition_comparison"
                trace.append({"field": field, "result": verdict, "reason": reason})
            values = [item["result"] for item in trace]
            combined = "false" if "false" in values else "unknown" if "unknown" in values else "true"
            result.update(status="SOURCE_CONDITIONS", source_conditions_result=combined,
                          trace=trace, citations=deepcopy(proposal["citations"]),
                          relation="available_via", ontology_candidate_id=version["data"]["instances"][0]["candidate_id"])
        if self._current() != current or self.store.get("state", "active_rules") != head:
            raise ValueError("SOURCE_OR_ONTOLOGY_CHANGED_DURING_QUERY")
        # Facts stay in memory; no query trace or personal answers enter SQLite.
        return result

    def rollback(self, target, expected_head, actor, *, confirmed=False):
        actor = self._actor(actor, confirmed)
        version = self._version(target)
        current = self._current() if version["data"]["instances"] else None
        if current: self._guard_version(version, current)
        with self.store.db(True) as db:
            if self.store.get("state", "active_rules", db) != expected_head:
                raise ValueError("BASE_VERSION_CHANGED")
            if current and self._current() != current: raise ValueError("SOURCE_CHANGED_DURING_ROLLBACK")
            self.store.put("state", "active_rules", target, db)
            self.store.audit("rollback", {"kind": FORMAT, "from": expected_head, "to": target, "actor": actor}, db)
        return target

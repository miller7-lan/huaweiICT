"""Reviewed, source-bound ontology plans for the bounded Nanjing archive demo.

Reuses the existing immutable ontology lifecycle. This module has no network,
model, publishing or native-run entry point and does not alter earlier evidence.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from gov_mcp.crossdoc_reasoning import SCENARIO_FACTS, load_bundle, solve
from gov_mcp.knowledge.store import digest
from gov_mcp.ontology_evolution import OntologyWorkspace


PROFILE = "nanjing-archive-interval-plan-v1"
ENTITY = {"name": "IntervalComparison", "label": "同起点工作日区间比较"}
FACT_CONTRACT = {"region": "南京市", "matter": "resident-id-renewal",
                 "household": ["inside_province", "outside_province"],
                 "remaining_validity_working_days": {"type": "integer", "minimum": 1, "maximum": 3660},
                 "normalized_same_start_and_unit": True, "lost_or_reported": False, "exam_expedited": False}
DOC_FIELDS = {"source_id", "url", "raw_sha256", "region", "matter", "segments",
              "published_at", "current_applicability", "effective_from", "effective_to",
              "formal_approval", "formally_published", "archive_mode", "scope_mapping", "revision"}


def integration_seed(seed):
    """A distinct seed prevents mixing the earlier Beijing workspace and scope."""
    return {**deepcopy(seed), "execution_profile": PROFILE}


def build_candidate(documents):
    if (not isinstance(documents, dict) or set(documents) != {"cycle", "old_card"}
            or any(not isinstance(doc, dict) or set(doc) - DOC_FIELDS for doc in documents.values())):
        raise ValueError("EXACT_PUBLIC_DOCUMENT_FIELDS_REQUIRED")
    parsed = solve(documents, SCENARIO_FACTS)
    if parsed["status"] != "derived":
        raise ValueError("COMPLETE_RECOGNIZED_ARCHIVE_CLAUSES_REQUIRED")
    operands = parsed["trace"][0]["inputs"]
    nodes = [
        {"id": "c1", "depends_on": [], "operator": "household_waiting",
         "output_type": "WorkingDayDuration", "source_key": "cycle",
         "parameters": {"base": operands["base_working_days"],
                        "outside_increment": operands["outside_increment_working_days"]}},
        {"id": "u1", "depends_on": ["c1"], "operator": "intersect_unexpired",
         "output_type": "WorkingDayDuration", "source_key": "old_card", "parameters": {}},
        {"id": "j1", "depends_on": ["c1", "u1"], "operator": "compare_coverage",
         "output_type": "ThreeValueCoverage", "source_key": "both", "parameters": {}},
    ]
    return {"schema_version": 1, "data_class": "public", "profile": PROFILE,
            "scope": {"region": "南京市", "matter": "resident-id-renewal"},
            "entity_type": deepcopy(ENTITY),
            "relation_type": {"name": "compares_coverage", "domain": "Matter",
                              "range": ENTITY["name"], "operator": "bounded_typed_dag_v1", "nodes": nodes},
            "source_documents": deepcopy(documents),
            "scope_mapping": deepcopy(parsed["scope_mapping"]),
            "fact_contract": deepcopy(FACT_CONTRACT),
            "fact_contract_note": "Registered fields; remaining interval is fictional and already normalized, not a policy value.",
            "construction_metrics": {"source_documents": 2, "source_paragraphs": 5,
                                     "derived_numeric_parameters": 2, "plan_nodes": 3, "model_calls": 0},
            "status": "pending_review", "extraction_method": "bounded_source_grammar_and_typed_plan_v1",
            "business_conclusion_status": "PAUSED_UNREVIEWED", "current_applicability": "unknown",
            "formal_approval": False, "formally_published": False}


class ArchivedCrossdocSource:
    def __init__(self, root):
        self.root = Path(root)

    def __call__(self):
        bundle = load_bundle(self.root)
        candidate = build_candidate(bundle["documents"])
        if load_bundle(self.root) != bundle:
            raise ValueError("SOURCE_CHANGED_DURING_PLAN_CONSTRUCTION")
        return {"candidate": candidate, "binding": bundle["binding"],
                "freshness_basis": "frozen_historical_archive_not_current_policy"}


class CrossdocOntologyWorkspace(OntologyWorkspace):
    @staticmethod
    def _implementation():
        return digest({name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                       for name in ("ontology_crossdoc.py", "ontology_evolution.py", "ontology_seed.py",
                                    "crossdoc_reasoning.py", "knowledge/store.py")})

    def _version(self, head):
        version = super()._version(head)
        if version["data"]["seed"].get("execution_profile") != PROFILE:
            raise ValueError("UNRELATED_ONTOLOGY_EXECUTION_PROFILE")
        if len(version["data"]["instances"]) > 1:
            raise ValueError("EXACT_SINGLE_REVIEWED_PLAN_REQUIRED")
        return version

    @staticmethod
    def _proposal(value):
        if not isinstance(value, dict) or value != build_candidate(value.get("source_documents")):
            raise ValueError("ONTOLOGY_PLAN_DIFFERS_FROM_SOURCE_DERIVED_CANDIDATE")
        return value

    @staticmethod
    def _execute(relation, documents, facts):
        """Execute registered nodes and source-derived operands, never cached answers."""
        values, trace = {}, []
        for node in relation["nodes"]:
            if any(parent not in values for parent in node["depends_on"]):
                raise ValueError("ONTOLOGY_DEPENDENCY_NOT_EXECUTED")
            operation, parameters = node["operator"], node["parameters"]
            if operation == "household_waiting" and node["output_type"] == "WorkingDayDuration":
                value = parameters["base"] + (parameters["outside_increment"]
                                               if facts["household"] == "outside_province" else 0)
                inputs = {"household": facts["household"], **parameters}
            elif operation == "intersect_unexpired" and node["output_type"] == "WorkingDayDuration":
                value = min(values[node["depends_on"][0]], facts["remaining_validity_working_days"])
                inputs = {"waiting": values["c1"], "remaining_validity_working_days": facts["remaining_validity_working_days"]}
            elif operation == "compare_coverage" and node["output_type"] == "ThreeValueCoverage":
                value = "false" if values[node["depends_on"][1]] < values[node["depends_on"][0]] else "unknown"
                inputs = {"waiting": values["c1"], "usable": values["u1"]}
            else:
                raise ValueError("UNREGISTERED_OR_INCOMPATIBLE_PLAN_OPERATOR")
            keys = ("cycle", "old_card") if node["source_key"] == "both" else (node["source_key"],)
            evidence = [{**{k: documents[key][k] for k in ("source_id", "url", "raw_sha256", "region", "matter")},
                         **deepcopy(segment)} for key in keys for segment in documents[key]["segments"]]
            values[node["id"]] = value
            trace.append({**deepcopy(node), "inputs": inputs, "output": value, "evidence": evidence})
        return values, trace

    def query(self, facts, *, expected_version=None):
        head = self.store.get("state", "active_rules")
        if expected_version is not None and head != expected_version:
            raise ValueError("ONTOLOGY_VERSION_CHANGED")
        version = self._version(head)
        # Reuse the established strict public fact/scope/unit/exception validator.
        # No private or unregistered input is returned or persisted.
        result = solve({}, facts)
        result.update(status="UNSUPPORTED_RELATION", reason="REVIEWED_ONTOLOGY_PLAN_REQUIRED",
                      version=head, trace=[], conclusion=None)
        if version["data"]["instances"]:
            current = self._current()
            self._guard_version(version, current)
            proposal = version["data"]["instances"][0]["proposal"]
            checked = solve({}, facts)
            if checked["reason"] != "BOTH_DISTINCT_SOURCE_DOCUMENTS_REQUIRED":
                result.update(status=checked["status"], reason=checked["reason"], facts=checked["facts"])
            else:
                # The registered definition, not a separate hardcoded graph, controls execution.
                relation = next(item for item in version["data"]["seed"]["relation_types"]
                                if item["name"] == proposal["relation_type"]["name"])
                values, trace = self._execute(relation, proposal["source_documents"], checked["facts"])
                result.update(status="derived", reason="REVIEWED_ONTOLOGY_PLAN_EXECUTED",
                              conclusion="cannot_establish_full_cycle_coverage" if values["j1"] == "false" else "coverage_not_disproved",
                              source_conditions_result=values["j1"], usual_waiting_working_days=values["c1"],
                              old_card_interval_working_days=values["u1"], trace=trace)
            instance = version["data"]["instances"][0]
            context = {"profile": PROFILE, "version": head, "candidate_id": instance["candidate_id"],
                       "source_binding": current["binding"], "implementation": self._implementation()}
            result["ontology_context"] = {**context, "binding": digest(context)}
            for node in result["trace"]:
                node["ontology_context_binding"] = result["ontology_context"]["binding"]
            if self._current() != current:
                raise ValueError("SOURCE_CHANGED_DURING_ONTOLOGY_EXECUTION")
        if self.store.get("state", "active_rules") != head or self._version(head) != version:
            raise ValueError("ONTOLOGY_CHANGED_DURING_EXECUTION")
        return result

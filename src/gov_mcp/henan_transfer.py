"""Reviewed Henan household-transfer archive plan; no current-policy or payment decision.

Second fixed scenario built from the reviewed-ontology workflow template. The Henan
household-registration norm (HTML) supplies the one-stop scope, the four-step fallback
procedure and the certificate reissue clause; the fee catalogue (XLSX) locates the
charging basis of the transfer certificate. The catalogue carries no amount, so the
plan never states one.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import unicodedata

from gov_mcp.json_output import exact_json_equal
from gov_mcp.knowledge.store import digest
from gov_mcp.ontology_evolution import OntologyWorkspace


PROFILE = "henan-archived-household-transfer-v1"
ENTITY = {"name": "ArchivedTransferEvidence", "label": "河南户口迁移归档依据包"}
FACTS = {"region": "河南省", "matter": "household-transfer", "branch": "intra_province_transfer",
         "purpose": "archive_explanation"}
DOC_FIELDS = {"source_id", "url", "original_sha256", "revision", "kind", "region", "matter",
              "segments", "current_applicability", "formal_approval", "formally_published", "archive_mode"}
IDENTITIES = {"catalog": ("HN-FEE-CATALOG-XLSX-2025", "xlsx", "河南省"),
              "norm": ("HN-NORM-PUBLISHER", "html", "河南省")}
CELL_IDS = ("A1", "D4", "E4", "F4", "G4", "B17", "D33", "E33", "F33", "G33", "D35", "F35", "G35")
ARTICLES = ("第四十八条", "第四十九条", "第五十条")
STEP_MARKS = ("（一）", "（二）", "（三）", "（四）")
NODE_IDS = ["one_stop_scope", "fallback_procedure", "certificate_reissue", "certificate_fee_basis", "archive_answer"]


def integration_seed(seed):
    return {**deepcopy(seed), "execution_profile": PROFILE}


def _compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _documents(documents):
    if not isinstance(documents, dict) or set(documents) != set(IDENTITIES):
        raise ValueError("TWO_DISTINCT_ARCHIVE_DOCUMENTS_REQUIRED")
    for key, (identifier, kind, region) in IDENTITIES.items():
        doc = documents[key]
        if not isinstance(doc, dict) or set(doc) != DOC_FIELDS:
            raise ValueError("EXACT_PUBLIC_DOCUMENT_FIELDS_REQUIRED")
        if ((doc["source_id"], doc["kind"], doc["region"], doc["matter"]) != (identifier, kind, region, FACTS["matter"])
                or not _hash(doc["original_sha256"])
                or doc["revision"] != "archived-original-" + doc["original_sha256"][:24]
                or not isinstance(doc["url"], str) or not doc["url"].startswith("https://")
                or doc["formal_approval"] is not False or doc["formally_published"] is not False
                or doc["archive_mode"] is not True or doc["current_applicability"] != "unknown"):
            raise ValueError("UNREVIEWED_ARCHIVE_IDENTITY_AND_SCOPE_REQUIRED")
        expected_kind = {"catalog": "literal_cell", "norm": "exact_html_article"}[key]
        expected_count = {"catalog": len(CELL_IDS), "norm": len(ARTICLES)}[key]
        segments = doc["segments"]
        if not isinstance(segments, list) or len(segments) != expected_count:
            raise ValueError("COMPLETE_ARCHIVE_CONTEXT_REQUIRED")
        locators = []
        for segment in segments:
            if (not isinstance(segment, dict) or set(segment) != {"locator", "text", "text_sha256", "evidence_kind"}
                    or not isinstance(segment["locator"], str) or not segment["locator"]
                    or not isinstance(segment["text"], str) or not 1 <= len(segment["text"]) <= 25000
                    or segment["evidence_kind"] != expected_kind
                    or hashlib.sha256(segment["text"].encode()).hexdigest() != segment["text_sha256"]):
                raise ValueError("EXACT_LOCATED_ARCHIVE_QUOTE_REQUIRED")
            locators.append(segment["locator"])
        if len(set(locators)) != len(locators):
            raise ValueError("DUPLICATE_ARCHIVE_LOCATOR")


def _steps(text):
    """Split article 49 at its four enumerated markers; every step keeps its exact wording."""
    positions = [text.find(mark) for mark in STEP_MARKS]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        raise ValueError("FOUR_ORDERED_PROCEDURE_STEPS_REQUIRED")
    ends = positions[1:] + [len(text)]
    steps = [text[start:end].strip() for start, end in zip(positions, ends)]
    # The fourth step runs to the end of its sentence; later school-specific lines stay out of the step.
    steps[3] = steps[3].split("\n")[0]
    return steps


def build_candidate(documents):
    _documents(documents)
    cells = {s["locator"].split("!")[-1]: s["text"] for s in documents["catalog"]["segments"]}
    compact = {key: _compact(value) for key, value in cells.items()}
    if (set(cells) != set(CELL_IDS) or compact["D4"] != "项目名称" or compact["E4"] != "资金管理方式"
            or compact["F4"] != "设立依据" or compact["G4"] != "收费标准" or compact["B17"] != "公安部门"
            or not compact["D33"].startswith("(3)户籍管理证件工本费") or "户口迁移证件" not in compact["D35"]
            or any(re.search(r"\d+(?:\.\d+)?元", compact[c]) for c in ("D33", "F33", "G33", "D35", "F35", "G35"))):
        raise ValueError("CATALOG_REFERENCES_NOT_INVENTED_AMOUNTS_REQUIRED")
    limited = re.search(r"\(限于([^)]+)\)", compact["D33"])
    if limited is None:
        raise ValueError("FEE_SCOPE_LIMITATION_REQUIRED")
    basis_codes = re.findall(r"[^,，、]+?〔\d{4}〕\d+号", cells["G33"])
    if not basis_codes or "户口登记条例" not in compact["F35"]:
        raise ValueError("FEE_BASIS_REFERENCES_REQUIRED")
    clauses = {s["locator"]: s["text"] for s in documents["norm"]["segments"]}
    if set(clauses) != set(ARTICLES):
        raise ValueError("THREE_COMPLETE_TRANSFER_ARTICLES_REQUIRED")
    one_stop = _compact(clauses["第四十八条"])
    if "省内户口迁移" not in one_stop or "一站式" not in one_stop or "拟迁入地派出所申请办理迁移手续" not in one_stop:
        raise ValueError("ONE_STOP_SCOPE_AND_CHANNEL_REQUIRED")
    if "不具备" not in _compact(clauses["第四十九条"]):
        raise ValueError("FALLBACK_PROCEDURE_REQUIRED")
    steps = _steps(clauses["第四十九条"])
    reissue = _compact(clauses["第五十条"])
    if "户口迁移证件遗失或者超过有效期限" not in reissue or "向签发公安机关申请补领、换领" not in reissue:
        raise ValueError("CERTIFICATE_REISSUE_CLAUSE_REQUIRED")
    nodes = [
        {"id": "one_stop_scope", "depends_on": [], "operator": "quoted_service_channel", "source_keys": ["norm"],
         "locators": ["第四十八条"], "parameters": {"channel_quote": clauses["第四十八条"]}},
        {"id": "fallback_procedure", "depends_on": ["one_stop_scope"], "operator": "ordered_procedure_steps",
         "source_keys": ["norm"], "locators": ["第四十九条"], "parameters": {"steps": steps}},
        {"id": "certificate_reissue", "depends_on": [], "operator": "quoted_service_channel", "source_keys": ["norm"],
         "locators": ["第五十条"], "parameters": {"channel_quote": clauses["第五十条"]}},
        {"id": "certificate_fee_basis", "depends_on": ["certificate_reissue"], "operator": "identify_fee_basis_without_amount",
         "source_keys": ["catalog"], "locators": ["Excel工作表行政事业性收费!" + c for c in CELL_IDS],
         "parameters": {"fee_item": cells["D33"].strip(), "certificate": cells["D35"].strip(),
                        "limited_to": limited.group(1), "basis_codes": [code.strip() for code in basis_codes],
                        "amount_in_catalog": None}},
        {"id": "archive_answer", "depends_on": ["fallback_procedure", "certificate_fee_basis"],
         "operator": "assemble_archive_evidence", "source_keys": ["norm", "catalog"], "locators": [], "parameters": {}},
    ]
    return {"schema_version": 1, "data_class": "public", "profile": PROFILE,
            "scope": deepcopy(FACTS), "fact_contract": deepcopy(FACTS), "entity_type": deepcopy(ENTITY),
            "relation_type": {"name": "assembles_transfer_archival_basis", "domain": "Matter", "range": ENTITY["name"],
                              "operator": "bounded_heterogeneous_archive_dag_v1", "nodes": nodes},
            "source_documents": deepcopy(documents), "status": "pending_review",
            "extraction_method": "bounded_grammar_over_pinned_originals",
            "construction_metrics": {"source_documents": 2, "located_segments": len(CELL_IDS) + len(ARTICLES),
                                     "plan_nodes": len(nodes), "model_calls": 0},
            "unresolved": ["current_applicability", "certificate_fee_amount", "local_transfer_policy_conditions",
                           "one_stop_availability_at_specific_offices", "required_materials_by_case",
                           "later_normative_changes"],
            "formal_approval": False, "formally_published": False,
            "business_conclusion_status": "PAUSED_UNREVIEWED", "current_applicability": "unknown"}


class HenanTransferWorkspace(OntologyWorkspace):
    @staticmethod
    def _implementation():
        base = Path(__file__).parent
        paths = [base / name for name in ("henan_transfer.py", "henan_transfer_sources.py", "ontology_evolution.py",
                                          "json_output.py", "knowledge/store.py", "knowledge_fetch.py")]
        return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def _version(self, head):
        version = super()._version(head)
        if version["data"]["seed"].get("execution_profile") != PROFILE or len(version["data"]["instances"]) > 1:
            raise ValueError("EXACT_HENAN_TRANSFER_SINGLE_ARCHIVE_PROFILE_REQUIRED")
        return version

    @staticmethod
    def _proposal(value):
        if not isinstance(value, dict) or not exact_json_equal(value, build_candidate(value.get("source_documents"))):
            raise ValueError("PLAN_DIFFERS_FROM_SOURCE_DERIVED_CANDIDATE")
        return value

    @staticmethod
    def _execute(relation, documents):
        values, trace = {}, []
        for node in relation["nodes"]:
            if any(parent not in values for parent in node["depends_on"]):
                raise ValueError("ARCHIVE_DEPENDENCY_NOT_EXECUTED")
            op, params = node["operator"], node["parameters"]
            if op == "quoted_service_channel":
                value = params["channel_quote"]
            elif op == "ordered_procedure_steps":
                if "一站式" not in values["one_stop_scope"]:
                    raise ValueError("ONE_STOP_SCOPE_REQUIRED_BEFORE_FALLBACK")
                value = {"applies_when": "不具备“一站式”迁移条件", "steps": list(params["steps"])}
            elif op == "identify_fee_basis_without_amount":
                if "户口迁移证件" not in values["certificate_reissue"]:
                    raise ValueError("REISSUE_CLAUSE_REQUIRED_BEFORE_FEE_BASIS")
                value = deepcopy(params)
            elif op == "assemble_archive_evidence":
                value = {"one_stop_scope_quote": values["one_stop_scope"],
                         "fallback_procedure": deepcopy(values["fallback_procedure"]),
                         "certificate_reissue_quote": values["certificate_reissue"],
                         "certificate_fee_basis": deepcopy(values["certificate_fee_basis"])}
            else:
                raise ValueError("UNREGISTERED_ARCHIVE_OPERATOR")
            wanted = set(node["locators"])
            evidence = [{**{field: documents[key][field] for field in ("source_id", "url", "original_sha256", "revision", "kind", "region")},
                         "quote": segment["text"], **{field: segment[field] for field in ("locator", "text_sha256", "evidence_kind")}}
                        for key in node["source_keys"] for segment in documents[key]["segments"]
                        if not wanted or segment["locator"] in wanted]
            values[node["id"]] = value
            trace.append({**deepcopy(node), "output": deepcopy(value), "evidence": evidence})
        return values["archive_answer"], trace

    def query(self, facts, *, expected_version=None):
        # No input facts or private fields are copied to output or persisted.
        invalid_facts = (not isinstance(facts, dict) or bool(set(facts) - set(FACTS))
                         or any(value is not None and not isinstance(value, str) for value in facts.values()))
        head = self.store.get("state", "active_rules")
        if expected_version is not None and head != expected_version:
            raise ValueError("ONTOLOGY_VERSION_CHANGED")
        version = self._version(head)
        result = {"status": "UNSUPPORTED_RELATION", "version": head, "archive_result": None, "trace": [],
                  "actual_fee_yuan": None, "formal_approval": False, "formally_published": False,
                  "current_applicability": "unknown", "business_conclusion_status": "PAUSED_UNREVIEWED"}
        current = None
        if invalid_facts:
            result.update(status="unknown", reason="UNREGISTERED_OR_PRIVATE_FACT")
        elif version["data"]["instances"]:
            current = self._current()
            self._guard_version(version, current)
            if any(facts.get(key) is None for key in FACTS):
                result.update(status="unknown", reason="MISSING_ARCHIVE_SCOPE")
            elif not exact_json_equal(facts, FACTS):
                result.update(status="out_of_scope", reason="ARCHIVE_EXPLANATION_ONLY")
            else:
                instance = version["data"]["instances"][0]
                proposal = instance["proposal"]
                relation = next(item for item in version["data"]["seed"]["relation_types"]
                                if item["name"] == proposal["relation_type"]["name"])
                archived, trace = self._execute(relation, proposal["source_documents"])
                context = {"profile": PROFILE, "version": head, "candidate_id": instance["candidate_id"],
                           "source_binding": current["binding"], "implementation": self._implementation()}
                result.update(status="derived_archive", archive_result=archived, trace=trace,
                              ontology_context={**context, "binding": digest(context)})
                for node in result["trace"]:
                    node["ontology_context_binding"] = result["ontology_context"]["binding"]
        if (current is not None and self._current() != current) or self.store.get("state", "active_rules") != head or self._version(head) != version:
            raise ValueError("SOURCE_OR_ONTOLOGY_CHANGED_DURING_QUERY")
        return result

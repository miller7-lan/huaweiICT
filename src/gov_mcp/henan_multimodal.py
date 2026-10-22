"""Reviewed Henan archive evidence plans; no current-policy or payment decision.

The table locates a normative basis, HTML supplies a historical fee branch, and
an attributed scan contributes separate historical service conditions. The scan
is never treated as a premise establishing the fee amount.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import unicodedata

from gov_mcp.json_output import exact_json_equal
from gov_mcp.knowledge.store import digest
from gov_mcp.ontology_evolution import OntologyWorkspace


PROFILE = "henan-archived-multimodal-v1"
ENTITY = {"name": "ArchivedRenewalEvidence", "label": "河南换领归档依据包"}
FACTS = {"region": "河南省", "matter": "resident-id-renewal", "branch": "expiry_renewal",
         "purpose": "archive_explanation"}
DOC_FIELDS = {"source_id", "url", "original_sha256", "revision", "kind", "region", "matter",
              "segments", "current_applicability", "formal_approval", "formally_published", "archive_mode"}
IDENTITIES = {"catalog": ("HN-FEE-CATALOG-XLSX-2025", "xlsx", "河南省"),
              "norm": ("MOF-FEE-2007", "html", "national"),
              "scan": ("HN-MEASURES-2021-PDF", "pdf", "河南省")}
CELL_IDS = ("A1", "D4", "E4", "F4", "G4", "B17", "D36", "E36", "F36", "G36")
BASIS_CODE = "财综〔2007〕34号"


def integration_seed(seed):
    return {**deepcopy(seed), "execution_profile": PROFILE}


def _compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _documents(documents):
    if not isinstance(documents, dict) or set(documents) != set(IDENTITIES):
        raise ValueError("THREE_DISTINCT_ARCHIVE_DOCUMENTS_REQUIRED")
    for key, (identifier, kind, region) in IDENTITIES.items():
        doc = documents[key]
        required = DOC_FIELDS | ({"visual_provenance"} if key == "scan" else set())
        if not isinstance(doc, dict) or set(doc) != required:
            raise ValueError("EXACT_PUBLIC_DOCUMENT_FIELDS_REQUIRED")
        if ((doc["source_id"], doc["kind"], doc["region"], doc["matter"]) !=
                (identifier, kind, region, FACTS["matter"]) or not _hash(doc["original_sha256"])
                or doc["revision"] != "archived-original-" + doc["original_sha256"][:24]
                or not isinstance(doc["url"], str) or not doc["url"].startswith("https://")
                or doc["formal_approval"] is not False or doc["formally_published"] is not False
                or doc["archive_mode"] is not True or doc["current_applicability"] != "unknown"):
            raise ValueError("UNREVIEWED_ARCHIVE_IDENTITY_AND_SCOPE_REQUIRED")
        segments = doc["segments"]
        expected_kind = {"catalog": "literal_cell", "norm": "exact_html_clause", "scan": "ai_visual_transcription"}[key]
        if not isinstance(segments, list) or len(segments) != {"catalog": 10, "norm": 5, "scan": 2}[key]:
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
    visual = documents["scan"]["visual_provenance"]
    if (not isinstance(visual, dict) or set(visual) != {"reviewer", "rendered_pages", "method", "human_review_completed"}
            or visual["human_review_completed"] is not False or not isinstance(visual["reviewer"], dict)
            or set(visual["reviewer"]) != {"kind", "name", "model", "exact_revision"}
            or visual["reviewer"]["kind"] != "ai" or not isinstance(visual["method"], str)):
        raise ValueError("ATTRIBUTED_AI_TRANSCRIPTION_NOT_HUMAN_REVIEW_REQUIRED")
    pages = visual["rendered_pages"]
    if (not isinstance(pages, list) or len(pages) != 2
            or any(not isinstance(p, dict) or set(p) != {"pdf_page", "image_sha256", "renderer"}
                   or type(p["pdf_page"]) is not int or not _hash(p["image_sha256"]) for p in pages)
            or [p["pdf_page"] for p in pages] != [5, 6]):
        raise ValueError("BOTH_BOUND_RENDERED_PAGES_REQUIRED")


def build_candidate(documents):
    _documents(documents)
    cells = {s["locator"].split("!")[-1]: _compact(s["text"]) for s in documents["catalog"]["segments"]}
    if (set(cells) != set(CELL_IDS) or cells["D4"] != "项目名称" or cells["F4"] != "设立依据"
            or cells["G4"] != "收费标准" or "居民身份证工本费" not in cells["D36"]
            or cells["B17"] != "公安部门" or BASIS_CODE not in cells["F36"] or BASIS_CODE not in cells["G36"]
            or re.search(r"\d+元", cells["G36"])):
        raise ValueError("CATALOG_REFERENCES_NOT_INVENTED_AMOUNTS_REQUIRED")
    clauses = {_compact(s["locator"]): _compact(s["text"]) for s in documents["norm"]["segments"]}
    if set(clauses) != {"正文段落" + str(i) for i in range(3, 8)}:
        raise ValueError("FIVE_COMPLETE_NORMATIVE_CLAUSES_REQUIRED")
    first = clauses["正文段落4"]
    pattern = (r"即向首次申领、换领第二代居民身份证的居民收取工本费每证(\d+)元;"
               r"向遗失补领、损坏换领第二代居民身份证的居民收取工本费每证(\d+)元;"
               r"为居民办理临时居民身份证收取工本费每证(\d+)元。")
    # NFKC normalizes Chinese semicolons; quoted source text itself is retained.
    match = re.search(pattern, first)
    suffix = "除上述收费外,各地不得自行规定收取或搭车收取其他任何费用,也无权自行制定和调整收费标准。"
    if (match is None or not first.startswith("一、公安机关") or first[match.end():] != suffix
            or "财综〔2004〕8号" not in first or "发改价格〔2003〕2322号" not in first):
        raise ValueError("COMPLETE_FEE_BRANCH_AND_LIMITATIONS_REQUIRED")
    fee_values = [int(value) for value in match.groups()]
    if any(not 0 < value <= 1000 for value in fee_values):
        raise ValueError("UNSUPPORTED_ARCHIVE_FEE_VALUE")
    scan = documents["scan"]["segments"]
    service, duration = (_compact(item["text"]) for item in scan)
    if ("省内通办" not in service or "公安机关签发的有效身份证件" not in service
            or "换领、补领" not in service or "(需挂失申报)" not in service
            or not service.endswith("申请换领、补领(需挂失申报)。")):
        raise ValueError("COMPLETE_SCAN_SERVICE_AND_AMBIGUOUS_EXCEPTION_REQUIRED")
    period = re.search(r"由过去承诺的(\d+)日\(自然日\)压缩至(\d+)日,为群众办理居民身份证节省更多时间。$", duration)
    if period is None or "从受理之日起到领证之日" not in duration:
        raise ValueError("COMPLETE_CROSS_PAGE_DURATION_AND_ORIGINAL_UNITS_REQUIRED")
    previous, quoted = map(int, period.groups())
    if not 0 < quoted <= previous <= 3660:
        raise ValueError("UNSUPPORTED_ARCHIVE_DURATION")
    nodes = [
        {"id": "catalog_basis", "depends_on": [], "operator": "identify_normative_reference", "source_keys": ["catalog"],
         "parameters": {"basis_code": BASIS_CODE}},
        {"id": "fee_branch", "depends_on": ["catalog_basis"], "operator": "ordinary_renewal_archive_fee", "source_keys": ["norm"],
         "parameters": {"ordinary_renewal_fee_yuan": fee_values[0], "lost_or_damaged_fee_yuan": fee_values[1], "temporary_fee_yuan": fee_values[2]}},
        {"id": "service_archive", "depends_on": [], "operator": "quoted_historical_service", "source_keys": ["scan"],
         "parameters": {"service_conditions_quote": scan[0]["text"], "duration_quote": scan[1]["text"],
                        "historical_duration": {"previous_natural_days": previous, "quoted_days": quoted,
                                                "unit": "day_unspecified", "interval": "acceptance_to_collection"}}},
        {"id": "archive_answer", "depends_on": ["fee_branch", "service_archive"], "operator": "assemble_archive_evidence",
         "source_keys": ["catalog", "norm", "scan"], "parameters": {}},
    ]
    return {"schema_version": 1, "data_class": "public", "profile": PROFILE,
            "scope": deepcopy(FACTS), "fact_contract": deepcopy(FACTS), "entity_type": deepcopy(ENTITY),
            "relation_type": {"name": "assembles_archival_basis", "domain": "Matter", "range": ENTITY["name"],
                              "operator": "bounded_heterogeneous_archive_dag_v1", "nodes": nodes},
            "source_documents": deepcopy(documents), "status": "pending_review",
            "extraction_method": "bounded_grammar_with_attributed_scan_transcription",
            "construction_metrics": {"source_documents": 3, "located_segments": 17, "plan_nodes": 4, "model_calls": 0},
            "unresolved": ["current_applicability", "fee_waivers", "local_office_acceptance", "later_normative_changes",
                           "scan_parenthesis_branch_scope", "new_duration_day_type", "current_required_materials"],
            "formal_approval": False, "formally_published": False,
            "business_conclusion_status": "PAUSED_UNREVIEWED", "current_applicability": "unknown"}


class HenanMultimodalWorkspace(OntologyWorkspace):
    @staticmethod
    def _implementation():
        base = Path(__file__).parent
        paths = [base / name for name in ("henan_multimodal.py", "henan_multimodal_sources.py", "ontology_evolution.py",
                                          "json_output.py", "knowledge/store.py", "knowledge_fetch.py")]
        return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def _version(self, head):
        version = super()._version(head)
        if version["data"]["seed"].get("execution_profile") != PROFILE or len(version["data"]["instances"]) > 1:
            raise ValueError("EXACT_HENAN_SINGLE_ARCHIVE_PROFILE_REQUIRED")
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
            if op == "identify_normative_reference":
                value = params["basis_code"]
            elif op == "ordinary_renewal_archive_fee":
                if values[node["depends_on"][0]] != BASIS_CODE:
                    raise ValueError("CATALOG_NORMATIVE_LINK_REQUIRED")
                value = params["ordinary_renewal_fee_yuan"]
            elif op == "quoted_historical_service":
                value = deepcopy(params)
            elif op == "assemble_archive_evidence":
                service = values["service_archive"]
                value = {"fee_basis_code": values["catalog_basis"], "ordinary_renewal_fee_yuan": values["fee_branch"],
                         "service_conditions_quote": service["service_conditions_quote"],
                         "duration_quote": service["duration_quote"], "historical_duration": deepcopy(service["historical_duration"])}
            else:
                raise ValueError("UNREGISTERED_ARCHIVE_OPERATOR")
            evidence = [{**{field: documents[key][field] for field in ("source_id", "url", "original_sha256", "revision", "kind", "region")},
                         "quote": segment["text"], **{field: segment[field] for field in ("locator", "text_sha256", "evidence_kind")}}
                        for key in node["source_keys"] for segment in documents[key]["segments"]]
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
                  "actual_fee_yuan": None, "promised_duration": None, "formal_approval": False,
                  "formally_published": False, "current_applicability": "unknown",
                  "business_conclusion_status": "PAUSED_UNREVIEWED"}
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

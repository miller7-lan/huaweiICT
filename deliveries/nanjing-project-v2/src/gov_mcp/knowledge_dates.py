"""Separate legal commencement from explicitly reviewed, source-dated service facts."""

import json
import re
from datetime import date


SERVICE_TYPES = {"official_service_information", "official_policy_explanation", "official_consultation_reply"}
SERVICE_KIND = "reported_service_information"
SERVICE_FIELDS = {"kind", "information_as_of", "base_revision", "original_sha256", "body_sha256",
                  "source_observation_sha256", "source_date_field", "source_date_value", "legal_commencement_established"}
PUBLICATION_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")


def reviewed_start(store, db, row, metadata):
    """Return the date used for filtering without inventing a legal effective date.

    A service fact needs a separate curator decision and the same original, text,
    and observation as its referenced base revision. Publication never implicitly
    turns into commencement, and this path cannot approve a normative document.
    """
    basis = metadata.get("date_basis")
    if basis is None:
        if not metadata.get("effective_from"):
            raise ValueError("Confirmed effective date or explicitly bound service date required")
        return date.fromisoformat(metadata["effective_from"]).isoformat()
    if (metadata.get("document_type") not in SERVICE_TYPES or not isinstance(basis, dict)
            or set(basis) != SERVICE_FIELDS or basis.get("kind") != SERVICE_KIND
            or basis.get("legal_commencement_established") is not False
            or metadata.get("effective_from") is not None or metadata.get("effective_to") is not None):
        raise ValueError("Separate, non-normative service-information date basis required")
    if any(not isinstance(basis[key], str) or not basis[key] for key in SERVICE_FIELDS - {"legal_commencement_established"}):
        raise ValueError("Explicit service-date binding strings required")
    observed = date.fromisoformat(basis["information_as_of"])
    if observed.isoformat() != basis["information_as_of"] or observed > date.today() or metadata.get("published_at") != observed.isoformat():
        raise ValueError("Established service publication date required; future or substituted dates rejected")
    from gov_mcp.knowledge import fingerprint
    base, _ = store._verify(db, basis["base_revision"], require_review=False)
    original_observation = json.loads(base["metadata"]).get("source_observation")
    if (base["document_id"] != row["document_id"] or base["sha256"] != row["sha256"]
            or base["sha256"] != basis["original_sha256"]
            or base["body_sha256"] != row["body_sha256"] or base["body_sha256"] != basis["body_sha256"]
            or not isinstance(original_observation, dict) or original_observation != metadata.get("source_observation")
            or fingerprint(original_observation) != basis["source_observation_sha256"]):
        raise ValueError("Service date must bind this exact document, original, body and source observation")
    field, literal = basis["source_date_field"], basis["source_date_value"]
    values = original_observation.get("fields", {}).get(field)
    if (field not in ("meta:pubdate", "block:page_date") or not isinstance(literal, str)
            or not PUBLICATION_VALUE.fullmatch(literal) or literal[:10] != observed.isoformat()
            or values != [literal]):
        raise ValueError("Unambiguous original publication field required; modification time is insufficient")
    return observed.isoformat()


def citation_date_fields(metadata):
    basis = metadata.get("date_basis")
    return {"date_basis": SERVICE_KIND, "information_as_of": basis["information_as_of"]} if basis else {}

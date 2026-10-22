"""Candidate formats for the optional model ports, not program tool receipts.

WiredRoles executes the deterministic workflow without calling these ports.
These contracts do not replace source matching, rule evaluation, current pause
checks or authenticated human review in that workflow or the native tools.
"""
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from gov_mcp.json_output import StrictOutput
from .models import Evidence, Identifier, Nonempty


def _false(value):
    # Pydantic Literal[False] alone also accepts integer zero in strict mode.
    if value is not False:
        raise ValueError("FALSE_BOOLEAN_REQUIRED")
    return value


def _true(value):
    if value is not True:
        raise ValueError("TRUE_BOOLEAN_REQUIRED")
    return value


FalseFlag = Annotated[Literal[False], BeforeValidator(_false)]
TrueFlag = Annotated[Literal[True], BeforeValidator(_true)]
Ids = Annotated[list[Identifier], Field(max_length=256)]
Notes = Annotated[list[Nonempty], Field(max_length=30)]
QueryTool = Literal["retrieve_registered_evidence", "verify_conditions"]
UpdateTool = Literal["classify_changes", "submit_candidates", "validate_candidates", "finish_run"]


class QueryPlanOutput(StrictOutput):
    allowed_tools: Annotated[list[QueryTool], Field(min_length=1, max_length=2)]
    max_rounds: Annotated[int, Field(ge=1, le=3)]
    evidence_ids: Ids
    unknown_user_fields: Ids


class RetrievedEvidenceOutput(StrictOutput):
    evidence: Annotated[list[Evidence], Field(max_length=256)]
    missing_evidence_ids: Ids


class ConditionCandidate(StrictOutput):
    requirement_id: Identifier
    result: Literal["true", "false", "unknown"]
    evidence_ids: Ids
    missing_fields: Ids


class QueryVerificationOutput(StrictOutput):
    condition_results: Annotated[list[ConditionCandidate], Field(max_length=256)]
    evidence_gaps: Ids
    unknown_user_fields: Ids
    needs_more: bool


class AnswerCandidate(StrictOutput):
    requirement_id: Identifier
    result: Literal["true", "false", "unknown", "paused"]
    evidence_ids: Ids
    missing_fields: Ids
    reason: Nonempty


class AnswerOutput(StrictOutput):
    requirements: Annotated[list[AnswerCandidate], Field(max_length=256)]
    unknown_user_fields: Ids
    paused_requirement_ids: Ids
    formal_approval: FalseFlag
    published: FalseFlag


class UpdatePlanOutput(StrictOutput):
    package_ids: Ids
    allowed_tools: Annotated[list[UpdateTool], Field(min_length=1, max_length=4)]
    requires_professional: TrueFlag


class ExtractedClaim(StrictOutput):
    # Same fields as knowledge.Engine.submit; exact substring/source membership
    # remain mandatory downstream checks and are not established by this schema.
    kind: Literal["Matter", "Condition", "Material", "Department", "Procedure", "StandardClause"]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    quote: Annotated[str, Field(min_length=1, max_length=1024)]
    source_segment_ids: Annotated[list[Identifier], Field(min_length=1, max_length=6)]


class ExtractionOutput(StrictOutput):
    candidates: Annotated[list[ExtractedClaim], Field(max_length=30)]


class UpdateVerificationOutput(StrictOutput):
    valid_structure: bool
    exact_evidence: bool
    conflicts: Notes
    omissions: Notes
    notes: Notes
    requires_professional: TrueFlag


class FinishRunOutput(StrictOutput):
    package_ids: Ids
    status: Literal["awaiting_review", "paused", "failed"]
    requires_professional: TrueFlag
    business_rules_published: FalseFlag


OUTPUT_CONTRACTS = {
    ("query_controller", "plan_query"): QueryPlanOutput,
    ("query_controller", "compose_answer"): AnswerOutput,
    ("retriever", "retrieve_registered_evidence"): RetrievedEvidenceOutput,
    ("query_verifier", "verify_conditions"): QueryVerificationOutput,
    ("update_controller", "plan_update"): UpdatePlanOutput,
    ("update_controller", "finish_run"): FinishRunOutput,
    ("extractor", "submit_candidates"): ExtractionOutput,
    ("update_verifier", "validate_candidates"): UpdateVerificationOutput,
}

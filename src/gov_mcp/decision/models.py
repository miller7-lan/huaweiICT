"""Validated knowledge objects; confirmed simulation records are hash-bound to reviews."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .logic import validate_expression

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")]
Nonempty = Annotated[str, Field(min_length=1, max_length=4000)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Refs = Annotated[list[Identifier], Field(min_length=1, max_length=64)]


def canonical_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Reviewed(Record):
    id: Identifier
    evidence_ids: Refs
    review_status: Literal["candidate", "confirmed"]
    review_id: Identifier | None = None


def reviewed_hash(record: Reviewed | dict, snapshot) -> str:
    """Bind review to the object, its rule graph, field definitions and original evidence.

    A fingerprint is integrity evidence, not a substitute for authenticated review.
    Simulated reviews are deliberately the only actor kind accepted in this phase.
    """
    data = snapshot.model_dump(mode="json") if isinstance(snapshot, BaseModel) else snapshot
    record_id = record.id if isinstance(record, BaseModel) else record["id"]
    objects = {data["matter"]["id"]: ("matter", data["matter"])}
    for collection in ("stages", "materials", "assets", "evidence", "clauses", "conditions", "branches", "applicability", "requirements", "aliases"):
        for item in data[collection]:
            objects[item["id"]] = (collection, item)
    basis, seen = [], set()
    def visit(key):
        if key in seen:
            return
        if key not in objects:
            raise ValueError("MISSING_DEPENDENCY")
        seen.add(key)
        kind, item = objects[key]
        basis.append({"kind":kind, "object":{k:v for k,v in item.items() if k != "review_id"}})
        references = list(item.get("evidence_ids", []))
        reference_fields = {"evidence": ("asset_id",), "branches": ("parent_id", "condition_id"),
                            "requirements": ("branch_id", "stage_id", "material_id", "condition_id", "applicability_id"),
                            "aliases": ("material_id",)}
        references.extend(item[field] for field in reference_fields.get(kind, ()) if item.get(field))
        if kind == "applicability":
            references.extend(item["clause_ids"])
        for ref in references:
            visit(ref)
    visit(record_id)
    visit(data["matter"]["id"])
    return canonical_hash({"object_id":record_id, "mode":data["mode"], "fields":data["fields"],
                           "basis":sorted(basis,key=lambda row:row["object"]["id"])})


class Review(Record):
    id: Identifier
    object_id: Identifier
    object_sha256: Digest
    actor_id: Identifier
    actor_kind: Literal["simulated_professional"]
    decision: Literal["accepted"]
    note: Nonempty


class FactField(Record):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    label: Nonempty
    kind: Literal["boolean", "number", "date", "string", "enum"]
    options: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_options(self):
        if self.kind == "enum":
            if not self.options or len(self.options) != len(set(self.options)) or any(not 0 < len(v) <= 200 for v in self.options):
                raise ValueError("INVALID_ENUM_OPTIONS")
        elif self.options:
            raise ValueError("OPTIONS_REQUIRE_ENUM")
        return self


class Versions(Record):
    documents: Identifier
    knowledge: Identifier
    ontology: Identifier


class Matter(Record):
    id: Identifier
    label: Nonempty
    region_id: Identifier


class Stage(Record):
    id: Identifier
    label: Nonempty
    order: Annotated[int, Field(ge=0, le=100)]


class Material(Record):
    id: Identifier
    label: Nonempty


class Asset(Record):
    id: Identifier
    source_url: Nonempty
    title: Nonempty
    revision: Identifier
    data_class: Literal["synthetic"]
    text: Annotated[str, Field(min_length=1, max_length=100000)]
    sha256: Digest


class Evidence(Record):
    id: Identifier
    asset_id: Identifier
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    quote: Nonempty
    locator: Nonempty


class Clause(Record):
    id: Identifier
    label: Nonempty
    evidence_ids: Refs


class Condition(Reviewed):
    expression: dict


class Branch(Reviewed):
    label: Nonempty
    parent_id: Identifier | None
    condition_id: Identifier
    order: Annotated[int, Field(ge=0, le=100)]


class Window(Record):
    confirmed: bool
    start_inclusive: date | None
    end_exclusive: date | None

    @model_validator(mode="after")
    def check_window(self):
        if self.start_inclusive and self.end_exclusive and self.start_inclusive >= self.end_exclusive:
            raise ValueError("INVALID_TIME_WINDOW")
        return self


class Applicability(Reviewed):
    region_id: Identifier
    clause_ids: Refs
    window: Window


class Requirement(Reviewed):
    branch_id: Identifier
    stage_id: Identifier
    material_id: Identifier
    action: Literal["提交", "交验", "交回", "出示"]
    condition_id: Identifier
    applicability_id: Identifier


class Alias(Reviewed):
    material_id: Identifier
    label: Nonempty


class KnowledgeSnapshot(Record):
    schema_version: Literal[1]
    mode: Literal["synthetic_engineering_only"]
    demo_date: date
    versions: Versions
    matter: Matter
    fields: Annotated[list[FactField], Field(min_length=1, max_length=64)]
    stages: Annotated[list[Stage], Field(min_length=1, max_length=32)]
    materials: Annotated[list[Material], Field(min_length=1, max_length=128)]
    assets: Annotated[list[Asset], Field(min_length=1, max_length=32)]
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=512)]
    clauses: Annotated[list[Clause], Field(min_length=1, max_length=128)]
    conditions: Annotated[list[Condition], Field(min_length=1, max_length=256)]
    branches: Annotated[list[Branch], Field(min_length=1, max_length=128)]
    applicability: Annotated[list[Applicability], Field(min_length=1, max_length=128)]
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=256)]
    aliases: list[Alias] = Field(default_factory=list, max_length=128)
    reviews: Annotated[list[Review], Field(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def integrity(self):
        collections = (self.stages, self.materials, self.assets, self.evidence, self.clauses, self.conditions,
                       self.branches, self.applicability, self.requirements, self.aliases, self.reviews)
        ids = [self.matter.id] + [record.id for records in collections for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_OBJECT_ID")
        if len({f.id for f in self.fields}) != len(self.fields):
            raise ValueError("DUPLICATE_FACT_FIELD")
        assets = {a.id: a for a in self.assets}
        evidence = {e.id: e for e in self.evidence}
        for asset in self.assets:
            if hashlib.sha256(asset.text.encode("utf-8")).hexdigest() != asset.sha256:
                raise ValueError("ASSET_FINGERPRINT_MISMATCH")
        for span in self.evidence:
            if span.asset_id not in assets or not 0 <= span.start < span.end <= len(assets[span.asset_id].text):
                raise ValueError("INVALID_EVIDENCE_RANGE")
            if assets[span.asset_id].text[span.start:span.end] != span.quote:
                raise ValueError("EVIDENCE_QUOTE_MISMATCH")
        referenced = [*self.conditions, *self.branches, *self.applicability, *self.requirements, *self.aliases, *self.clauses]
        reviews = {r.id: r for r in self.reviews}
        fields = {f.id: f.model_dump() for f in self.fields}
        for item in referenced:
            if len(item.evidence_ids) != len(set(item.evidence_ids)) or any(ref not in evidence for ref in item.evidence_ids):
                raise ValueError("INVALID_EVIDENCE_REFERENCE")
            if isinstance(item, Reviewed) and item.review_status == "confirmed":
                review = reviews.get(item.review_id)
                if not review or review.object_id != item.id or review.object_sha256 != reviewed_hash(item, self):
                    raise ValueError("REVIEW_BINDING_MISMATCH")
            if isinstance(item, Condition) and item.review_status == "confirmed":
                validate_expression(item.expression, fields)
        conditions = {c.id: c for c in self.conditions}
        branches = {b.id: b for b in self.branches}
        scopes = {a.id: a for a in self.applicability}
        clauses = {c.id: c for c in self.clauses}
        materials = {m.id: m for m in self.materials}
        stages = {s.id: s for s in self.stages}
        for branch in self.branches:
            deps = [conditions.get(branch.condition_id)]
            if branch.parent_id:
                deps.append(branches.get(branch.parent_id))
            self._dependencies(branch, deps)
            seen, current = set(), branch
            while current:
                if current.id in seen:
                    raise ValueError("BRANCH_CYCLE")
                seen.add(current.id)
                current = branches.get(current.parent_id)
        for scope in self.applicability:
            if len(scope.clause_ids) != len(set(scope.clause_ids)) or any(ref not in clauses for ref in scope.clause_ids):
                raise ValueError("INVALID_CLAUSE_REFERENCE")
        for requirement in self.requirements:
            if requirement.material_id not in materials or requirement.stage_id not in stages:
                raise ValueError("INVALID_MATERIAL_OR_STAGE")
            self._dependencies(requirement, [branches.get(requirement.branch_id), conditions.get(requirement.condition_id),
                                             scopes.get(requirement.applicability_id)])
        for alias in self.aliases:
            if alias.material_id not in materials:
                raise ValueError("INVALID_ALIAS_TARGET")
        names = {m.label: m.id for m in self.materials}
        for alias in self.aliases:
            if alias.review_status == "confirmed":
                if alias.label in names and names[alias.label] != alias.material_id:
                    raise ValueError("AMBIGUOUS_CONFIRMED_ALIAS")
                names[alias.label] = alias.material_id
        return self

    @staticmethod
    def _dependencies(item, dependencies):
        if any(dep is None for dep in dependencies):
            raise ValueError("MISSING_DEPENDENCY")
        if item.review_status == "confirmed" and any(dep.review_status != "confirmed" for dep in dependencies):
            raise ValueError("UNCONFIRMED_DEPENDENCY")

    def json_fingerprint(self):
        return canonical_hash(self.model_dump(mode="json"))

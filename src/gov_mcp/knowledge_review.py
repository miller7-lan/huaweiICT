"""Attributed evaluation provenance; hashes verify binding, not a reviewer's truthfulness."""

import json
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import fingerprint


def question_fingerprint(question):
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", question)).casefold()
    return fingerprint(normalized.encode("utf-8"))


def development_registry(db):
    return [row[0] for row in db.execute("SELECT question_sha256 FROM evaluation_development_questions ORDER BY question_sha256")]


def register_development_questions(store, questions, source):
    """Append hashes before a development run; existing evidence is never deleted."""
    if store.read_only or not _text(source) or len(source) > 300:
        raise ValueError("Writable runtime and bounded development provenance required")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 10000 or any(not _text(q) or len(q) > 1000 for q in questions):
        raise ValueError("Bounded development questions required")
    values = sorted({question_fingerprint(q) for q in questions})
    from gov_mcp.knowledge_lock import process_lock
    with process_lock(store.root / "review-contract.lock") as acquired:
        if not acquired:
            raise ValueError("Another review contract operation is running")
        with store.db() as db:
            before = len(development_registry(db))
            db.executemany("INSERT OR IGNORE INTO evaluation_development_questions VALUES(?,?,?)",
                           [(value, source, time.time()) for value in values])
            registry = development_registry(db)
            store.log(db, "development_questions_registered", {"source": source, "input_questions": len(questions),
                "new_question_hashes": len(registry)-before, "registry_sha256": fingerprint(registry)})
    return {"question_count": len(registry), "registry_sha256": fingerprint(registry)}


def upgrade_review_contract(store, expected_policy, expected_registry, maintainer, confirmed=False):
    """Explicit local upgrade; fixed thresholds, old policy retained, zero live heads required."""
    from gov_mcp.knowledge import encode
    from gov_mcp.knowledge_lock import process_lock
    from gov_mcp.knowledge_quality import publication_policy, BUSINESS_POLICY, ATTRIBUTED_BUSINESS_POLICY
    if store.read_only or confirmed is not True or not _text(maintainer):
        raise ValueError("Writable runtime and explicit attributed maintainer confirmation required")
    with process_lock(store.root / "review-contract.lock") as acquired:
        if not acquired:
            raise ValueError("Another review contract operation is running")
        # Serialize against publication, which also takes an immediate write transaction.
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = publication_policy(store)
            registry = development_registry(db)
            heads = db.execute("SELECT COUNT(*) FROM heads").fetchone()[0]
            if fingerprint(current) != expected_policy or fingerprint(registry) != expected_registry:
                raise ValueError("Policy or development registry changed since inspection")
            if current == ATTRIBUTED_BUSINESS_POLICY:
                return {"changed": False, "policy_sha256": fingerprint(current), "development_registry_sha256": expected_registry}
            if current != BUSINESS_POLICY or heads:
                raise ValueError("Upgrade requires legacy business policy and no live publication heads")
            history = store.root / "policy-history"
            history.mkdir(exist_ok=True)
            previous = history / (fingerprint(current)+".json")
            if previous.exists():
                if json.loads(previous.read_text(encoding="utf-8")) != current:
                    raise ValueError("Previous policy archive mismatch")
            else:
                with previous.open("x", encoding="utf-8") as stream:
                    stream.write(encode(current))
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=store.root,
                        prefix="publication-policy-", suffix=".tmp", delete=False) as stream:
                    stream.write(encode(ATTRIBUTED_BUSINESS_POLICY))
                    temporary = stream.name
                Path(temporary).replace(store.root / "publication-policy.json")
            finally:
                if temporary:
                    Path(temporary).unlink(missing_ok=True)
            report = {"changed": True, "previous_policy_sha256": fingerprint(current),
                "policy_sha256": fingerprint(ATTRIBUTED_BUSINESS_POLICY), "development_registry_sha256": expected_registry,
                "registered_development_questions": len(registry), "maintainer": maintainer.strip(),
                "quality_thresholds_unchanged": True, "publication_performed": False}
            store.log(db, "review_contract_upgraded", report)
        return report


def reference_fingerprint(report):
    return fingerprint({"snapshot_binding": report["snapshot_binding"],
        "author": report["reference_author"], "frozen_at": report["reference_frozen_at"],
        "cases": [{key: case[key] for key in ("id", "question", "reference_answer", "expected_outcome", "relevant_evidence")}
                  for case in report["cases"]]})


def observation_fingerprint(report):
    return fingerprint({"snapshot_binding": report["snapshot_binding"], "run": report["observed_run"],
        "cases": [{key: case[key] for key in ("id", "question", "observed_answer", "observed_outcome", "retrieved_top5",
                                              "trace_sha256", "input_sha256")} for case in report["cases"]]})


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value)) and value != "0"*64


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Timezone-aware review chronology required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Timezone-aware review chronology required") from None
    if parsed.tzinfo is None:
        raise ValueError("Timezone-aware review chronology required")
    return parsed.astimezone(timezone.utc)


def _actor(actor):
    if not isinstance(actor, dict) or actor.get("kind") not in ("human", "ai") or not _text(actor.get("name")):
        raise ValueError("Explicit human or AI actor identity required")
    if actor["kind"] == "ai":
        if (not _text(actor.get("model")) or not _text(actor.get("run_id")) or "model_revision" not in actor
                or (actor["model_revision"] is not None and not _text(actor["model_revision"]))):
            raise ValueError("AI actor needs model, run and explicit known or unknown model revision")
    return actor["kind"]


def validate_attributed_review(report):
    """Schema 2 is explicit about AI identity and checks declared artifact separation."""
    if (report.get("schema") != 2 or report.get("status") != "reviewed"
            or report.get("dataset_role") != "independent_business_acceptance"):
        raise ValueError("Attributed independent business acceptance report required")
    if any(key in report for key in ("reference_author_kind", "reference_reviewer")):
        raise ValueError("Do not mix legacy human fields with attributed review actors")
    author = _actor(report.get("reference_author"))
    reviewer = _actor(report.get("reviewer"))
    if type(report.get("human_review_completed")) is not bool or report["human_review_completed"] != (reviewer == "human"):
        raise ValueError("Human review flag must match the explicit reviewer kind")
    run = report.get("observed_run")
    if not isinstance(run, dict) or not _text(run.get("id")):
        raise ValueError("Actual observed run identity required")
    _actor(run.get("executor"))
    if run.get("reference_answers_in_model_input") is not False:
        raise ValueError("Reference answers must be separated from tested model inputs")
    frozen, started, completed, reviewed = (_time(value) for value in (
        report.get("reference_frozen_at"), run.get("started_at"), run.get("completed_at"), report.get("reference_reviewed_at")))
    if not frozen <= started <= completed <= reviewed <= datetime.now(timezone.utc):
        raise ValueError("Reference must be frozen before the observed run and reviewed afterwards")
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases or any(not isinstance(case, dict) for case in cases):
        raise ValueError("Attributed evaluation cases required")
    for case in cases:
        if "human_judgment" in case or case.get("review_judgment") != "pass" or not _text(case.get("review_notes")):
            raise ValueError("Each case needs attributed judgment and notes without legacy human fields")
        if not _digest(case.get("trace_sha256")) or not _digest(case.get("input_sha256")):
            raise ValueError("Each observation needs actual trace and input fingerprints")
        if not _text(case.get("question")):
            raise ValueError("Each observation needs a question")
    holdout = report.get("holdout")
    if not isinstance(holdout, dict) or holdout.get("method") != "normalized_question_hash_disjoint":
        raise ValueError("Explicit development question registry and holdout check required")
    development = holdout.get("development_question_sha256")
    if (not isinstance(development, list) or any(not _digest(value) for value in development)
            or len(set(development)) != len(development) or development != sorted(development)
            or holdout.get("registry_sha256") != fingerprint(development)):
        raise ValueError("Sorted unique development question registry fingerprint required")
    if any(question_fingerprint(case["question"]) in development for case in cases):
        raise ValueError("Development questions cannot be reused as held-out acceptance questions")
    normalized = [question_fingerprint(case["question"]) for case in cases]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Normalized acceptance questions must be distinct")
    try:
        valid = (_digest(report.get("reference_sha256")) and _digest(report.get("observation_sha256"))
                 and report["reference_sha256"] == reference_fingerprint(report)
                 and report["observation_sha256"] == observation_fingerprint(report))
    except KeyError:
        raise ValueError("Complete frozen reference and observed artifacts required") from None
    if not valid:
        raise ValueError("Frozen reference or observed artifact fingerprint mismatch")
    return {"reference_author_kind": author, "reviewer_kind": reviewer,
        "human_review_completed": reviewer == "human", "review_contract": "attributed_v2",
        "reference_sha256": report["reference_sha256"], "observation_sha256": report["observation_sha256"],
        "development_registry_sha256": holdout["registry_sha256"],
        "basis": "Attributed review judgments and bound artifacts; actor declarations, semantic independence and policy truth need separate evidence"}

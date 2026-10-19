"""Snapshot-bound evaluation records with explicit review contracts and fixed thresholds."""

import json
import math
import time
from datetime import datetime, timezone

from gov_mcp.knowledge import encode, fingerprint


BUSINESS_POLICY = {"schema": 1, "mode": "business", "minimum_cases": 30, "minimum_recall_at_5": 0.9,
                   "required_claim_support": 1.0, "require_sources_fresh": True}
ATTRIBUTED_BUSINESS_POLICY = dict(BUSINESS_POLICY, schema=2, review_contract="attributed_v2")


def publication_policy(store):
    policy = json.loads((store.root / "publication-policy.json").read_text(encoding="utf-8"))
    if policy == {"schema": 1, "mode": "engineering_fixture"}:
        return policy
    if policy not in (BUSINESS_POLICY, ATTRIBUTED_BUSINESS_POLICY):
        raise ValueError("Recognized publication policy required; business quality thresholds cannot be weakened")
    return policy


def binding(store, db, version):
    snapshot = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
    if not snapshot:
        raise ValueError("Candidate version not found")
    revisions, sources = [], {}
    for revision in json.loads(snapshot["members"]):
        row, digest = store._verify(db, revision)
        document = db.execute("SELECT site_id FROM documents WHERE id=?", (row["document_id"],)).fetchone()
        config = db.execute("SELECT config FROM sites WHERE id=?", (document[0],)).fetchone()[0]
        sources[document[0]] = fingerprint(json.loads(config))
        revisions.append({"revision": revision, "digest": digest})
    from gov_mcp.knowledge_discovery import watched_source_ids
    region, matter = json.loads(snapshot["scope"])
    for source_id in watched_source_ids(db, region, matter):
        config = db.execute("SELECT config FROM sites WHERE id=?", (source_id,)).fetchone()[0]
        sources[source_id] = fingerprint(json.loads(config))
    from gov_mcp.knowledge_attachments import dependencies
    for source_id in dependencies(db, list(sources)):
        row = db.execute("SELECT config FROM sites WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("Attachment dependency source missing")
        sources[source_id] = fingerprint(json.loads(row[0]))
    policy = publication_policy(store)
    bound = {"version": version, "scope": snapshot["scope"], "members": revisions,
             "source_configs": sources, "policy": policy}
    if policy.get("review_contract") == "attributed_v2":
        from gov_mcp.knowledge_review import development_registry
        bound["development_questions"] = development_registry(db)
    return fingerprint(bound)


def validate_report(store, db, version, report):
    if not isinstance(report, dict):
        raise ValueError("Evaluation report must be an object")
    policy = publication_policy(store)
    if policy["mode"] != "business":
        raise ValueError("Business evaluation recording requires business mode")
    if report.get("snapshot_binding") != binding(store, db, version) or report.get("version") != version:
        raise ValueError("Evaluation does not bind this exact snapshot, sources and policy")
    if policy.get("review_contract") == "attributed_v2":
        from gov_mcp.knowledge_review import validate_attributed_review, development_registry
        provenance = validate_attributed_review(report)
        if report["holdout"]["development_question_sha256"] != development_registry(db):
            raise ValueError("Holdout registry must match all registered development questions")
        judgment_key = "review_judgment"
    else:
        if (report.get("status") != "human_reviewed" or report.get("reference_author_kind") != "human"
                or report.get("dataset_role") != "independent_business_acceptance"
                or not isinstance(report.get("reference_reviewer"), str) or not report["reference_reviewer"].strip()):
            raise ValueError("Independent human reference answers and explicit review required")
        provenance = {"reference_author_kind": "human", "reviewer_kind": "human", "human_review_completed": True,
            "review_contract": "legacy_human_v1", "basis": "Human-attested references and judgments; this validator does not establish policy truth by itself"}
        judgment_key = "human_judgment"
    reviewed = datetime.fromisoformat(report.get("reference_reviewed_at", ""))
    age = (datetime.now(timezone.utc) - reviewed.astimezone(timezone.utc)).total_seconds()
    if reviewed.tzinfo is None or age < 0 or age > 30*86400:
        raise ValueError("Reference review must be recent and timezone-aware")
    cases = report.get("cases")
    if not isinstance(cases, list) or len(cases) < policy["minimum_cases"]:
        raise ValueError("At least 30 independently reviewed cases required")
    members = set(json.loads(db.execute("SELECT members FROM versions WHERE id=?", (version,)).fetchone()[0]))
    known = {(r[0], r[1]) for r in db.execute("SELECT revision_id,locator FROM chunks") if r[0] in members}
    ids, questions, recalls = set(), set(), []
    supported, claims, answered, stopped = 0, 0, 0, 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Evaluation case must be an object")
        identifier, question = case.get("id"), case.get("question")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids or not isinstance(question, str) or not question.strip() or question.strip() in questions:
            raise ValueError("Unique case ids and independent questions required")
        ids.add(identifier)
        questions.add(question.strip())
        if (case.get(judgment_key) != "pass" or not isinstance(case.get("reference_answer"), str) or not case["reference_answer"].strip()
                or not isinstance(case.get("observed_answer"), str) or not case["observed_answer"].strip()):
            raise ValueError("Every case needs a reviewed reference and passing judgment")
        expected, observed = case.get("expected_outcome"), case.get("observed_outcome")
        if expected not in ("answer", "stop") or observed != expected:
            raise ValueError("Unexpected answer/stop behavior")
        for key in ("unsupported_claims", "wrong_region_claims", "expired_rules_as_current", "missing_critical_requirements"):
            if type(case.get(key)) is not int or case[key] != 0:
                raise ValueError("Zero unsupported, mis-scoped, expired or missing critical requirements required")
        total, correct = case.get("claim_count"), case.get("supported_claim_count")
        if type(total) is not int or type(correct) is not int or total < 0 or correct < 0 or correct != total:
            raise ValueError("All counted claims must be supported")
        relevant, retrieved = case.get("relevant_evidence"), case.get("retrieved_top5")
        if not isinstance(relevant, list) or not isinstance(retrieved, list) or len(retrieved) > 5:
            raise ValueError("Located reference evidence and at most five retrieved items required")
        def references(values):
            result = set()
            for value in values:
                if (not isinstance(value, dict) or set(value) != {"revision", "locator"}
                        or not isinstance(value["revision"], str) or not isinstance(value["locator"], str)):
                    raise ValueError("Evidence must identify a revision and locator")
                result.add((value["revision"], value["locator"]))
            if not result <= known or len(result) != len(values):
                raise ValueError("Unknown, out-of-snapshot or duplicate evidence")
            return result
        reference_set, retrieved_set = references(relevant), references(retrieved)
        if expected == "answer":
            if not reference_set or total == 0:
                raise ValueError("Answer cases need relevant reference evidence and counted claims")
            answered += 1
            recalls.append(len(reference_set & retrieved_set)/len(reference_set))
        else:
            stopped += 1
            if total or retrieved:
                raise ValueError("Stop cases cannot assert business claims or return answer evidence")
        supported += correct
        claims += total
    # Require both positive and negative coverage so all-stop reports cannot pass a live corpus.
    if members and (answered == 0 or stopped == 0):
        raise ValueError("Both answer and stop coverage required")
    recall = math.fsum(recalls)/len(recalls) if recalls else None
    if recall is not None and recall < policy["minimum_recall_at_5"]:
        raise ValueError("Mean per-answer Recall@5 is below 90 percent")
    return {"case_count": len(cases), "answer_cases": answered, "stop_cases": stopped,
            "mean_recall_at_5": recall, "recall_denominator": len(recalls),
            "supported_claim_count": supported, "claim_count": claims,
            "claim_support_rate": supported/claims if claims else None,
            **provenance}


def verify_publication(store, db, version):
    policy = publication_policy(store)
    snapshot = db.execute("SELECT members FROM versions WHERE id=?", (version,)).fetchone()
    members = json.loads(snapshot[0])
    if policy["mode"] == "engineering_fixture":
        for revision in members:
            row, _ = store._verify(db, revision)
            metadata = json.loads(row["metadata"])
            source = db.execute("SELECT s.config FROM sites s JOIN documents d ON d.site_id=s.id WHERE d.id=?", (row["document_id"],)).fetchone()
            if metadata["document_type"] != "synthetic" or json.loads(source[0]).get("access_review", {}).get("actor_kind") != "synthetic":
                raise ValueError("Engineering fixture mode refuses real source documents")
        return {"mode": "engineering_fixture", "business_approval": False}
    evaluated = db.execute("SELECT * FROM snapshot_evaluations WHERE version_id=? ORDER BY rowid DESC LIMIT 1", (version,)).fetchone()
    if not evaluated:
        raise ValueError("Independent business evaluation required before publication")
    report = json.loads(evaluated["report"])
    if fingerprint(report) != evaluated["report_sha256"]:
        raise ValueError("Evaluation report fingerprint mismatch")
    metrics = validate_report(store, db, version, report)
    source_ids = {r[0] for r in db.execute("SELECT DISTINCT d.site_id FROM documents d JOIN revisions r ON r.document_id=d.id WHERE r.id IN (" + ",".join("?" for _ in members) + ")", members)}
    from gov_mcp.knowledge_discovery import watched_source_ids
    region, matter = json.loads(db.execute("SELECT scope FROM versions WHERE id=?", (version,)).fetchone()[0])
    source_ids.update(watched_source_ids(db, region, matter))
    from gov_mcp.knowledge_fetch import validate_config
    for source_id in source_ids:
        source = db.execute("SELECT * FROM sites WHERE id=?", (source_id,)).fetchone()
        config = json.loads(source["config"])
        validate_config(config)
        if not config["enabled"] or source["paused_reason"] or source["failures"] > 0 or not source["last_success"] or time.time()-source["last_success"] > config["interval_seconds"]+900:
            raise ValueError("Enabled, available and recently checked sources required for publication")
    return {"mode": "business", "evaluation": evaluated["id"], "metrics": metrics}

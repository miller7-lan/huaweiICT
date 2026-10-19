"""One-shot, literal-preserving rebinding to three already reviewed sources."""
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from holdout_provenance import read, sealed, semantic_projection, source_binding, validate_migration
from holdout_provenance_v4 import (ARCHIVE_V3, ARCHIVE_NAMES, BASE_COMMIT, EXPECTED_REVISIONS,
                                   MIGRATION_V4, literal_anchors, v4_evidence)
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+"\n").encode("utf-8")


def main():
    if (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != BASE_COMMIT
            or (ROOT/ARCHIVE_V3).exists() or (ROOT/MIGRATION_V4).exists()):
        raise ValueError("Expected the inspected v3 base commit and a new archive; do not replay")
    files = {name:ROOT/"evaluation/knowledge"/name for name in ARCHIVE_NAMES if name != "holdout-draft-integrity.json"}
    files["holdout-draft-integrity.json"] = ROOT/"evidence/knowledge-update/holdout-draft-integrity.json"
    for path in files.values():
        if path.read_bytes() != subprocess.check_output(["git", "show", "HEAD:"+path.relative_to(ROOT).as_posix()], cwd=ROOT):
            raise ValueError("Current v3 inputs must match the inspected committed bytes")
    before = read(files["holdout-business-reference-draft.json"])
    questions = read(files["holdout-business-questions-draft.json"])
    screening = read(files["holdout-semantic-screening.json"])
    assert sealed(before) and sealed(screening) and before["draft_revision"] == 3
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    after = deepcopy(before)
    stamp = datetime.now(timezone.utc).isoformat()
    after.update(draft_revision=4, provenance_migrated_at=stamp, provenance_migration={"path":MIGRATION_V4.as_posix()})
    with store.db() as db:
        validate_migration(ROOT, store, db, before)
        for key, expected in EXPECTED_REVISIONS.items():
            old = before["sources"][key]
            after["sources"][key] = source_binding(store, db, old["source_id"], old["url"])
            assert after["sources"][key]["revision"] == expected
        for old, new in zip(before["cases"], after["cases"], strict=True):
            new["background_source_anchors"] = literal_anchors(db, old, after["sources"])
            new["proposed_relevant_evidence"] = [] if new["proposed_outcome_after_qualified_publication"] == "stop" else [
                {"revision":anchor["revision"], "locator":anchor["locator"]} for anchor in new["background_source_anchors"]]
        after["content_sha256"] = fingerprint({k:v for k,v in after.items() if k != "content_sha256"})
        evidence = v4_evidence(store, db, before, after)
    affected = [case["id"] for case in evidence["cases"] if case["before_sha256"] != case["after_sha256"]]
    assert affected == ["HB01", "HB31", "HB08", "HB11", "HB35"]
    archive = ROOT/ARCHIVE_V3
    archive.mkdir(parents=True)
    for name, path in files.items():
        (archive/name).write_bytes(path.read_bytes())
    manifest = {"schema":1, "source_commit":BASE_COMMIT, "created_at":stamp,
        "purpose":"Immutable v3 draft and its stale-binding receipt before literal-only v4 migration; no acceptance.",
        "files":[{"path":(ARCHIVE_V3/name).as_posix(), "sha256":fingerprint(files[name].read_bytes())} for name in sorted(files)]}
    manifest["content_sha256"] = fingerprint(manifest)
    (archive/"manifest.json").write_bytes(serialize(manifest))
    stale = ARCHIVE_V3/"holdout-draft-integrity.json"
    ledger = {"schema":1, "kind":"holdout_provenance_migration_not_acceptance", "created_at":stamp,
        "reviewer":"Codex AI", "reviewer_kind":"ai", "human_review_completed":False, "acceptance_authorized":False,
        "base_archive_manifest_sha256":manifest["content_sha256"], "evidence":evidence,
        "previous_integrity_report":{"path":stale.as_posix(), "sha256":fingerprint((ROOT/stale).read_bytes())},
        "limits":"All 30 question/date/oracle/outcome/prerequisite/independence judgments are unchanged. Only three source bindings and five cases' revision/ordinal pointers change. This neither certifies independence nor freezes references, establishes unresolved currentness, authorizes model execution or publishes knowledge."}
    ledger["content_sha256"] = fingerprint(ledger)
    questions["reference_draft_sha256"] = after["content_sha256"]
    screening.update(screening_revision=4, assessed_at=stamp,
        reference_contents_sha256=after["content_sha256"], reference_semantics_sha256=fingerprint(semantic_projection(after)),
        provenance_migration_sha256=ledger["content_sha256"])
    screening["actor"]["run_id"] = "manual-provenance-semantic-rebind-20261017-v4"
    screening["inputs"] += " v4 rebind: verified the five affected case anchors against three actual scoped source reviews; all parent text, character spans, questions, dates, oracles, prerequisites and per-case screening judgments remain identical. The unreferenced national clause 76 retains its own pending watch."
    screening["provenance_review"] = {"reviewer":"Codex AI", "affected_case_ids":affected, "case_semantics_unchanged":True,
        "per_case_screening_judgments_unchanged":True, "literal_span_changes":0,
        "source_truth_or_batch_independence_approved_by_this_rebind":False}
    screening["history_artifacts"] += [{"path":item["path"], "sha256":item["sha256"]} for item in manifest["files"]]
    screening["history_artifacts"] += [{"path":(ARCHIVE_V3/"manifest.json").as_posix(), "sha256":fingerprint((archive/"manifest.json").read_bytes())},
        {"path":MIGRATION_V4.as_posix(), "sha256":fingerprint(serialize(ledger))}]
    screening["content_sha256"] = fingerprint({k:v for k,v in screening.items() if k != "content_sha256"})
    assert screening["cases"] == read(files["holdout-semantic-screening.json"])["cases"]
    staging = store.root/"holdout-drafts/provenance-v4-20261017"
    ensure_private_directory(staging)
    payloads = [(ROOT/MIGRATION_V4, ledger), (files["holdout-business-reference-draft.json"], after),
                (files["holdout-business-questions-draft.json"], questions), (files["holdout-semantic-screening.json"], screening)]
    for destination, value in payloads:
        temporary = staging/destination.name
        if temporary.exists():
            raise ValueError("Private staging exists; inspect a partial operation rather than replay")
        temporary.write_bytes(serialize(value))
    for destination, _ in payloads:
        os.replace(staging/destination.name, destination)
    with store.db() as db:
        validate_migration(ROOT, store, db, after)
    print(json.dumps({"draft_revision":4, "affected_cases":affected, "source_bindings_updated":3,
        "questions_dates_or_oracles_changed":0, "literal_span_changes":0, "reference_frozen":False,
        "independence_certified":False, "acceptance_authorized":False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

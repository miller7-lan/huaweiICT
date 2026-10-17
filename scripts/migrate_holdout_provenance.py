"""Apply the reviewed v3 source migration, without running or approving a test."""

import json
import os
from copy import deepcopy
from datetime import datetime, timezone

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from holdout_provenance import (ARCHIVE, MIGRATION, STALE_RECEIPT, archive_reference, expected_anchors,
                                migration_evidence, semantic_projection, source_binding)
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    reference_path = ROOT / "evaluation/knowledge/holdout-business-reference-draft.json"
    question_path = ROOT / "evaluation/knowledge/holdout-business-questions-draft.json"
    screen_path = ROOT / "evaluation/knowledge/holdout-semantic-screening.json"
    before, manifest = archive_reference(ROOT)
    if json.loads(reference_path.read_text(encoding="utf-8")) != before or (ROOT / MIGRATION).exists():
        raise ValueError("Expected unchanged active v2 and a new migration record; do not replay")
    questions = json.loads(question_path.read_text(encoding="utf-8"))
    screening = json.loads(screen_path.read_text(encoding="utf-8"))
    assert screening == json.loads((ROOT / ARCHIVE / screen_path.name).read_text(encoding="utf-8"))
    assert questions == json.loads((ROOT / ARCHIVE / question_path.name).read_text(encoding="utf-8"))
    store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    after = deepcopy(before)
    stamp = datetime.now(timezone.utc).isoformat()
    after.update(draft_revision=3,provenance_migrated_at=stamp,provenance_migration={"path":MIGRATION.as_posix()})
    after["freeze_blockers"][0] = "仍须完成全部引用来源、材料关系及业务标准核定；本库无已发布业务快照"
    with store.db() as db:
        for key,source,url in [("D26",before["sources"]["D26"]["source_id"],before["sources"]["D26"]["url"]),
                               ("LAW","ID-AMENDMENT-JT-LEAD","https://jtgl.beijing.gov.cn/jgj/jgxx/flfg/fl/203777/index.html")]:
            after["sources"][key] = source_binding(store,db,source,url)
        for old,new in zip(before["cases"],after["cases"],strict=True):
            new["background_source_anchors"] = expected_anchors(db,old,after["sources"])
            new["proposed_relevant_evidence"] = [] if new["proposed_outcome_after_qualified_publication"] == "stop" else [
                {"revision":a["revision"],"locator":a["locator"]} for a in new["background_source_anchors"]]
        after["content_sha256"] = fingerprint({k:v for k,v in after.items() if k != "content_sha256"})
        evidence = migration_evidence(store,db,before,after)
    ledger = {"schema":1,"kind":"holdout_provenance_migration_not_acceptance","created_at":stamp,
              "reviewer":"Codex AI","reviewer_kind":"ai","human_review_completed":False,
              "acceptance_authorized":False,"base_archive_manifest_sha256":manifest["content_sha256"],"evidence":evidence,
              "previous_integrity_report":{"path":STALE_RECEIPT.as_posix(),"sha256":fingerprint((ROOT/STALE_RECEIPT).read_bytes())},
              "limits":"Only source provenance changes. Same author has seen development history; source approval does not approve the reference, certify independence, freeze a snapshot or authorize acceptance execution."}
    ledger["content_sha256"] = fingerprint(ledger)
    questions["reference_draft_sha256"] = after["content_sha256"]
    screening.update(screening_revision=3,assessed_at=stamp,
        reference_contents_sha256=after["content_sha256"],reference_semantics_sha256=fingerprint(semantic_projection(after)),
        provenance_migration_sha256=ledger["content_sha256"])
    screening["actor"]["run_id"] = "manual-provenance-semantic-rebind-20261017-v3"
    screening["inputs"] += " v3 rebind: reviewed all 11 affected case references against the migrated clauses; questions, dates, oracles, semantic distinctions and all per-case screening judgments are unchanged. HB06 removes only unapproved explanatory background; the complete first service measure retains both branches."
    screening["provenance_review"] = {"reviewer":"Codex AI","affected_case_ids":[c["id"] for c in evidence["cases"] if c["before_sha256"] != c["after_sha256"]],
        "case_semantics_unchanged":True,"per_case_screening_judgments_unchanged":True,
        "source_truth_or_batch_independence_approved_by_this_rebind":False}
    screening["history_artifacts"] += [{"path":item["path"],"sha256":item["sha256"]} for item in manifest["files"]]
    staging = store.root / "holdout-drafts/provenance-v3-20261017"
    ensure_private_directory(staging)
    serialized_ledger = (json.dumps(ledger,ensure_ascii=False,indent=2)+"\n").encode()
    screening["history_artifacts"].append({"path":MIGRATION.as_posix(),"sha256":fingerprint(serialized_ledger)})
    screening["content_sha256"] = fingerprint({k:v for k,v in screening.items() if k != "content_sha256"})
    payloads = [(ROOT/MIGRATION,ledger),(reference_path,after),(question_path,questions),(screen_path,screening)]
    for destination,value in payloads:
        temporary = staging / destination.name
        if temporary.exists():
            raise ValueError("Private staging already exists; inspect rather than replay")
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    for destination,_ in payloads:
        os.replace(staging/destination.name,destination)
    print(json.dumps({"draft_revision":3,"changed_cases":len(screening["provenance_review"]["affected_case_ids"]),
        "questions_or_oracles_changed":0,"background_anchors":sum(len(c["background_source_anchors"]) for c in after["cases"]),
        "recorded_source_reviews":sum(b["business_approval"] for b in after["sources"].values()),
        "reference_frozen":False,"acceptance_authorized":False},ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Audit the actual privacy rebind and reject semantic or approval tampering."""
import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from holdout_provenance import read, semantic_projection, validate_migration
from holdout_provenance_v5 import ARCHIVE_V4, BASE_COMMIT, archive_v4, v5_evidence
from nexent_api import ROOT, RUNTIME


def main():
    store = KnowledgeStore(RUNTIME/"knowledge-update", read_only=True)
    before, manifest = archive_v4(ROOT)
    after = read(ROOT/"evaluation/knowledge/holdout-business-reference-draft.json")
    if after.get("draft_revision") != 5:
        raise SystemExit("This receipt is limited to the v5 privacy rebind; preserve history after migration")
    checks = {}
    with store.db() as db:
        validate_migration(ROOT, store, db, after)
        evidence = v5_evidence(store, db, before, after)
        checks["complete_v2_v3_v4_v5_source_and_literal_history_valid"] = True
        checks["v4_archive_is_exact_committed_bytes"] = all(
            (ROOT/item["path"]).read_bytes() == subprocess.check_output(["git", "show", BASE_COMMIT+":"+
                ("evidence/knowledge-update/" if Path(item["path"]).name == "holdout-draft-integrity.json" else "evaluation/knowledge/")+
                Path(item["path"]).name], cwd=ROOT) for item in manifest["files"])
        checks["thirty_question_date_oracle_and_prerequisite_semantics_unchanged"] = semantic_projection(before) == semantic_projection(after)
        checks["per_case_independence_judgments_unchanged"] = (
            read(ROOT/ARCHIVE_V4/"holdout-semantic-screening.json")["cases"] ==
            read(ROOT/"evaluation/knowledge/holdout-semantic-screening.json")["cases"])
        checks["only_four_privacy_cases_changed"] = [c["id"] for c in evidence["cases"] if c["before_sha256"] != c["after_sha256"]] == ["HB21", "HB22", "HB38", "HB24"]
        checks["same_original_and_all_literal_spans_unchanged"] = evidence["literal_span_changes"] == evidence["evidence_insertions_or_removals"] == 0
        mutations = {
            "reject_answer_tuning":lambda d:d["cases"][0].update(reference_answer_draft="changed"),
            "reject_query_date_shift":lambda d:d["cases"][0].update(query_date="2026-10-18"),
            "reject_clearing_unknowns":lambda d:d["cases"][0].update(unresolved_prerequisites=[]),
            "reject_extra_evidence":lambda d:d["cases"][0]["background_source_anchors"].append(deepcopy(d["cases"][0]["background_source_anchors"][0])),
            "reject_unreviewed_use_binding":lambda d:d["sources"]["USE"].update(business_approval=False),
            "reject_altered_other_source":lambda d:d["sources"]["F18"].update(business_approval=False),
        }
        for name, mutate in mutations.items():
            changed = deepcopy(after)
            mutate(changed)
            changed["content_sha256"] = fingerprint({k:v for k,v in changed.items() if k != "content_sha256"})
            try:
                v5_evidence(store, db, before, changed)
            except ValueError:
                checks[name] = True
            else:
                checks[name] = False
        checks["no_new_review_evaluation_version_or_publication"] = db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 9 and all(
            db.execute("SELECT count(*) FROM "+table).fetchone()[0] == 0 for table in ["snapshot_evaluations", "versions", "heads"])
    try:
        store.candidate("郑州市二七区", "demo-id-renewal")
    except ValueError as error:
        checks["unresolved_business_sources_still_block_candidate"] = str(error) == "Unreviewed or changed content"
    else:
        checks["unresolved_business_sources_still_block_candidate"] = False
    report = {"schema":1, "captured_at":datetime.now(timezone.utc).isoformat(), "passed":all(checks.values()), "checks":checks,
        "source_bindings_updated":1, "affected_cases":["HB21", "HB22", "HB38", "HB24"], "network_or_model_requests":0,
        "reference_frozen":False, "independence_certified":False, "business_publications":0, "reviewer_kind":"ai"}
    (ROOT/"evidence/knowledge-update/provenance-v5-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

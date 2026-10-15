"""Verify a full bundle of the actual candidate runtime in an isolated private restore."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_backup import create_bundle, restore_bundle
from private_runtime import ensure_private_directory


ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_parent = store.root / "backups"
    restore_parent = ROOT / ".runtime/knowledge-update-restores"
    ensure_private_directory(backup_parent)
    ensure_private_directory(restore_parent)
    backup = create_bundle(store, backup_parent / stamp)
    restored = restore_bundle(backup["directory"], restore_parent / stamp)
    reader = KnowledgeStore(restored["restored_to"], read_only=True)
    original_status, restored_status = store.status(), reader.status()
    checks = {"document_revision_chunk_counts_match": all(original_status["counts"][key] == restored_status["counts"][key] for key in ("documents", "revisions", "chunks", "latest_chunks", "versions", "heads")),
        "discovery_cycles_and_deferred_targets_match": original_status["discovery"] == restored_status["discovery"],
        "attachment_provenance_graph_matches": original_status["attachments"] == restored_status["attachments"],
        "all_current_candidate_metadata_match": original_status["pending_review"] == restored_status["pending_review"],
        "restored_runtime_has_no_running_worker_claims": all(worker["progress"] == "completed" for worker in restored_status["workers"]),
        "restored_runtime_has_no_mcp_token": not (reader.root / "knowledge-mcp-token.txt").exists(),
        "services_not_started_by_restore": restored["services_started"] is False}
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "backup": backup, "restore": restored, "published_snapshots_available": original_status["counts"]["heads"] > 0,
        "scope": "Actual candidate data/original/configuration recovery; published-answer replay is separately tested using synthetic fixtures, not claimed for zero official releases"}
    (ROOT / "evidence/knowledge-update/full-backup-restore.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "originals": backup["referenced_originals"], "files": backup["files"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Exercise the complete update loop with explicitly synthetic files, never official rules."""

import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore, encode
from gov_mcp.knowledge_fetch import schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = ROOT / ".runtime/knowledge-update-checks" / uuid.uuid4().hex
    store = KnowledgeStore.initialize(runtime)
    (store.root / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    config = {"id": "controlled-fixture", "label": "纯模拟来源，未访问互联网", "enabled": True,
              "origin": "https://fixtures.invalid", "allowed_paths": ["/docs/"], "seeds": ["https://fixtures.invalid/docs/guide"],
              "interval_seconds": 3600, "delay_seconds": 5, "content_selector": {"id": "body"},
              "metadata": {"title": "模拟文件，不是真实办事依据", "region": "controlled-test", "matter": "test-only",
                           "document_type": "synthetic", "effective_from": None, "effective_to": None,
                           "usage_basis": "Self-authored synthetic engineering fixture"},
              "access_review": {"approved": True, "reviewer": "controlled-engineering-fixture", "actor_kind": "synthetic", "usage_basis": "Self-authored fixture; zero real requests",
                                "checked_at": datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    class Fixture:
        content = "测试事项需要模拟材料甲。"
        def fetch(self, source, url):
            return 200, {"content-type": "text/html"}, ('<article id="body"><p>'+self.content+'</p></article>').encode(), url
    fixture = Fixture()
    checks, records = {}, []

    def release():
        revision = store.status()["pending_review"][0]
        metadata = dict(revision["metadata"], effective_from="2026-01-01")
        amended = store.amend(revision["id"], metadata)
        store.review(amended, "controlled-engineering-fixture", True)
        candidate = store.candidate("controlled-test", "test-only")
        return store.publish(candidate, "controlled-engineering-fixture", True)

    def query(version=None):
        return store.search("测试事项", "controlled-test", "test-only", version=version, as_of="2026-10-15")

    clock = time.time()
    schedule(store, now=clock)
    worker_once(store, fixture, now=clock)
    checks["collected_candidate_not_published"] = query()["message_code"] == "NO_PUBLISHED_KNOWLEDGE"
    first = release()
    records.append(query())
    checks["first_release_quoted_fixture_a"] = "模拟材料甲" in records[-1]["citations"][0]["quote"]
    fixture.content = "测试事项需要模拟材料乙。"
    schedule(store, now=clock+3700)
    worker_once(store, fixture, now=clock+3700)
    checks["changed_content_blocks_current_answer_until_review"] = query()["message_code"] == "UPDATE_PENDING_REVIEW"
    second = release()
    records.append(query())
    checks["new_query_uses_new_release"] = records[-1]["knowledge_version"] == second and "模拟材料乙" in records[-1]["citations"][0]["quote"]
    old = query(first)
    checks["pinned_history_keeps_first_quote_and_update_hint"] = old["update_available"] and "模拟材料甲" in old["citations"][0]["quote"]
    records.append(old)
    schedule(store, now=clock+7400)
    before = store.status()["counts"]["revisions"]
    worker_once(store, fixture, now=clock+7400)
    checks["unchanged_repeat_creates_no_revision"] = store.status()["counts"]["revisions"] == before
    class FailedFixture:
        def fetch(self, source, url):
            raise OSError("Controlled failure after a successful check; no network used")
    schedule(store, now=clock+11100)
    worker_once(store, FailedFixture(), now=clock+11100)
    failed = query()
    checks["new_failure_blocks_despite_recent_success"] = failed["message_code"] == "SOURCE_CHECK_REQUIRED" and not failed["citations"] and any(source["failed_check"] and not source["stale"] for source in failed["source_freshness"])
    class UnchangedFixture:
        def fetch(self, source, url):
            return 304, {}, b"", url
    worker_once(store, UnchangedFixture(), now=clock+11161)
    recovered = query()
    checks["successful_recheck_clears_failure_without_new_revision"] = recovered["status"] == "OK" and recovered["knowledge_version"] == second and store.status()["counts"]["revisions"] == before
    store.rollback(first, "controlled-engineering-fixture", True)
    rolled_back = query()
    checks["rollback_restores_pointer_without_bypassing_reviewed_newer_rules"] = rolled_back["knowledge_version"] == first and rolled_back["message_code"] == "UPDATE_AWAITING_PUBLICATION" and not rolled_back["citations"]
    store.backup(runtime / "backup.sqlite3")
    restored = KnowledgeStore.initialize(runtime / "restored")
    shutil.copy2(runtime / "backup.sqlite3", restored.path)
    shutil.copytree(runtime / "originals", restored.root / "originals")
    restored_query = restored.search("测试事项", "controlled-test", "test-only")
    checks["backup_and_originals_restore_query"] = restored_query["knowledge_version"] == first and restored_query["message_code"] == "UPDATE_AWAITING_PUBLICATION"
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
              "checks": checks, "records": records, "counts": store.status()["counts"],
              "real_network_requests": 0, "official_knowledge_published": 0,
              "scope": "Synthetic engineering acquisition/review/publication/query/rollback/restore only; no business accuracy claim"}
    destination = ROOT / "evidence/knowledge-update/controlled-flow.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Knowledge controlled flow", "PASS" if report["passed"] else "FAIL", checks)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

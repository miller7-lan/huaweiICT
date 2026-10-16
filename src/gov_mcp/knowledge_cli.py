"""Local maintenance commands. No network requests occur during init or source registration."""

import argparse
import json
import time
from pathlib import Path

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import schedule, worker_once


def main():
    parser = argparse.ArgumentParser(description="Isolated knowledge update maintenance")
    parser.add_argument("--runtime", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    development = sub.add_parser("register-development-questions")
    development.add_argument("dataset", type=Path)
    development.add_argument("--source", required=True)
    contract = sub.add_parser("upgrade-review-contract")
    contract.add_argument("--expected-policy-sha256", required=True)
    contract.add_argument("--expected-registry-sha256", required=True)
    contract.add_argument("--maintainer", required=True)
    contract.add_argument("--confirmed", action="store_true")
    impact = sub.add_parser("impact")
    impact.add_argument("revision")
    impact.add_argument("--base")
    impact.add_argument("--include-excerpts", action="store_true")
    sub.add_parser("duplicate-candidates")
    register = sub.add_parser("register")
    register.add_argument("config", type=Path)
    resume = sub.add_parser("resume")
    resume.add_argument("source")
    resume.add_argument("--confirmed", action="store_true")
    worker = sub.add_parser("worker")
    worker.add_argument("--cycles", type=int, default=1, help="0 runs continuously")
    worker.add_argument("--interval", type=float, default=60)
    worker.add_argument("--max-jobs", type=int, default=20)
    query = sub.add_parser("search")
    query.add_argument("question")
    query.add_argument("--region", required=True)
    query.add_argument("--matter", required=True)
    query.add_argument("--version")
    query.add_argument("--as-of")
    amend = sub.add_parser("amend")
    amend.add_argument("revision")
    amend.add_argument("metadata", type=Path)
    review = sub.add_parser("review")
    review.add_argument("revision")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--confirmed", action="store_true")
    exclude = sub.add_parser("exclude")
    exclude.add_argument("revision")
    exclude.add_argument("--reviewer", required=True)
    exclude.add_argument("--reason", required=True)
    exclude.add_argument("--confirmed", action="store_true")
    reopen = sub.add_parser("reopen")
    reopen.add_argument("revision")
    reopen.add_argument("--reviewer", required=True)
    reopen.add_argument("--confirmed", action="store_true")
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--region", required=True)
    candidate.add_argument("--matter", required=True)
    for name in ("evaluation-binding", "publication-check"):
        check = sub.add_parser(name)
        check.add_argument("version")
    evaluation = sub.add_parser("record-evaluation")
    evaluation.add_argument("version")
    evaluation.add_argument("report", type=Path)
    evaluation.add_argument("--reviewer", required=True)
    evaluation.add_argument("--confirmed", action="store_true")
    for name in ("publish", "rollback"):
        action = sub.add_parser(name)
        action.add_argument("version")
        action.add_argument("--maintainer", required=True)
        action.add_argument("--confirmed", action="store_true")
    backup = sub.add_parser("backup")
    backup.add_argument("target", type=Path)
    bundle = sub.add_parser("backup-bundle")
    bundle.add_argument("target", type=Path)
    restore = sub.add_parser("restore-bundle")
    restore.add_argument("source", type=Path)
    restore.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        store = KnowledgeStore.initialize(args.runtime) if args.command == "init" else KnowledgeStore(args.runtime, read_only=args.command in ("impact", "duplicate-candidates"))
        value = None
        if args.command in ("init", "status"):
            value = store.status()
        elif args.command == "register-development-questions":
            from gov_mcp.knowledge_review import register_development_questions
            dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
            if not isinstance(dataset, dict) or not isinstance(dataset.get("cases"), list) or any(not isinstance(case, dict) or "question" not in case for case in dataset["cases"]):
                raise ValueError("Question dataset with cases required")
            value = register_development_questions(store, [case["question"] for case in dataset["cases"]], args.source)
        elif args.command == "upgrade-review-contract":
            from gov_mcp.knowledge_review import upgrade_review_contract
            value = upgrade_review_contract(store, args.expected_policy_sha256, args.expected_registry_sha256, args.maintainer, args.confirmed)
        elif args.command == "impact":
            from gov_mcp.knowledge_impact import revision_impact
            value = revision_impact(store, args.revision, args.base, args.include_excerpts)
        elif args.command == "duplicate-candidates":
            from gov_mcp.knowledge_impact import duplicate_candidates
            value = duplicate_candidates(store)
        elif args.command == "register":
            for config in json.loads(args.config.read_text(encoding="utf-8"))["sources"]:
                store.register(config)
            value = store.status()
        elif args.command == "resume":
            store.resume(args.source, args.confirmed)
            value = {"resumed": args.source}
        elif args.command == "worker":
            if args.cycles < 0 or args.interval < 1 or not 1 <= args.max_jobs <= 100:
                raise ValueError("Invalid worker limits")
            cycle = 0
            while args.cycles == 0 or cycle < args.cycles:
                schedule(store)
                for _ in range(args.max_jobs):
                    if not worker_once(store, owner="knowledge-worker"):
                        break
                cycle += 1
                if args.cycles == 0 or cycle < args.cycles:
                    time.sleep(args.interval)
            value = store.status()
        elif args.command == "search":
            value = store.search(args.question, args.region, args.matter, args.version, args.as_of)
        elif args.command == "amend":
            value = {"revision_id": store.amend(args.revision, json.loads(args.metadata.read_text(encoding="utf-8")))}
        elif args.command == "review":
            store.review(args.revision, args.reviewer, args.confirmed)
            value = {"reviewed": args.revision}
        elif args.command == "exclude":
            value = {"excluded_revision": store.exclude(args.revision, args.reviewer, args.reason, args.confirmed)}
        elif args.command == "reopen":
            value = {"reopened_revision": store.reopen(args.revision, args.reviewer, args.confirmed)}
        elif args.command == "candidate":
            value = {"version_id": store.candidate(args.region, args.matter)}
        elif args.command == "evaluation-binding":
            value = {"version": args.version, "snapshot_binding": store.evaluation_binding(args.version)}
        elif args.command == "record-evaluation":
            value = store.record_evaluation(args.version, json.loads(args.report.read_text(encoding="utf-8")), args.reviewer, args.confirmed)
        elif args.command == "publication-check":
            value = store.publication_check(args.version)
        elif args.command == "publish":
            value = {"version_id": store.publish(args.version, args.maintainer, args.confirmed)}
        elif args.command == "rollback":
            store.rollback(args.version, args.maintainer, args.confirmed)
            value = {"restored": args.version}
        elif args.command == "backup":
            value = store.backup(args.target)
        elif args.command == "backup-bundle":
            from gov_mcp.knowledge_backup import create_bundle
            value = create_bundle(store, args.target)
        elif args.command == "restore-bundle":
            from gov_mcp.knowledge_backup import restore_bundle
            value = restore_bundle(args.source, args.target)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Knowledge update stopped: {error}\n")


if __name__ == "__main__":
    main()

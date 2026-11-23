"""Gate a cross-document evaluation round before any model call and decide what it may be called.

Reads a declaration of the round (who wrote the questions, who reviewed them, which
prompt profile is frozen) and checks what can be checked mechanically: the declared
commit and a clean working tree, the question and reference files, the corpus, and,
for native runs, the profile of the running service. Nothing calls a model.

The result is one of:
  BLOCKED      a condition without which the run must not start; fix it first
  formal       every independence condition is declared true; the run may be called 正式评测
  development  the run may start, but each unmet condition is listed under "discounts"
               and the run must be called 开发对比 everywhere, with those discounts stated

Declared facts come from the person organising the round. A fact that is missing or
not exactly true counts as unmet, so an unknown never upgrades a run.
"""
import argparse
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from compare_frozen_flow_v3 import ARMS, check_reference  # noqa: E402
from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow  # noqa: E402

# Declared fact -> what the report must say when it is not true.
INDEPENDENCE = {
    "questions_written_without_access_to_flow_code_or_diagnostics":
        "出题方接触过被测代码或诊断结果",
    "questions_written_outside_the_session_that_runs_the_evaluation":
        "出题与执行评测在同一会话或同一环境完成",
    "topic_overlap_reviewed_by_a_person":
        "题意是否与旧题重合，尚未经人工复核",
    "reference_checked_against_sources_by_a_person":
        "参考答案与必答要点尚未经人工对照原文复核",
    "two_reviewers_who_did_not_write_the_questions":
        "没有两位未参与出题的评审",
    "organizer_does_not_rate":
        "组织者（保管密钥者）参与了评分",
}
MIN_SHARE = {"should_stop": 0.25}  # a round without enough should-stop questions cannot test stopping


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def evaluate(declared, *, commit, dirty, questions=None, reference=None, corpus=None, health=None):
    """Pure decision from declared facts and observed state; returns the preflight record."""
    blocked, discounts, observed = [], [], {"commit": commit, "dirty_files": dirty}
    rules = declared.get("rules")
    if rules not in flow.RULE_PROFILES:
        blocked.append("RULES_NOT_FROZEN: declare rules as strict or light before any run")
    arms = declared.get("arms") or []
    if not arms or any(arm not in ARMS for arm in arms):
        blocked.append("ARMS_NOT_FROZEN: declare arms from " + ", ".join(ARMS))
    if declared.get("commit") != commit:
        blocked.append(f"COMMIT_MISMATCH: declared {declared.get('commit')}, checked out {commit}")
    if dirty:
        blocked.append("WORKING_TREE_NOT_CLEAN: " + ", ".join(dirty[:5]))
    if questions is None:
        blocked.append("QUESTIONS_REQUIRED")
    if reference is None:
        blocked.append("REFERENCE_REQUIRED: a frozen reference decides expected outcomes, never earlier results")
    if questions is not None:
        try:
            items = frozen_qa.validate_questions(json.loads(Path(questions).read_text("utf-8")))
            observed["questions"] = len(items)
            observed["questions_sha256"] = sha256(Path(questions).read_bytes()).hexdigest()
            if reference is not None:
                frozen = check_reference(reference, items)
                observed["reference"] = frozen
                for category, share in MIN_SHARE.items():
                    if frozen["expected"]["stop"] < share * len(items):
                        blocked.append(f"TOO_FEW_{category.upper()}_QUESTIONS")
                if any(count == 0 for count in frozen["categories"].values()):
                    discounts.append("四类题型没有全部覆盖")
        except (OSError, ValueError, KeyError, TypeError) as error:
            blocked.append("QUESTIONS_OR_REFERENCE_INVALID: " + str(error)[:120])
    if corpus is None:
        blocked.append("CORPUS_REQUIRED")
    else:
        try:
            units = json.loads(Path(corpus).read_text("utf-8"))["units"]
            if not isinstance(units, list) or not units:
                raise ValueError("Frozen corpus has no units")
            unit_sha = frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True))
            observed["corpus_units"], observed["corpus_unit_sha256"] = len(units), unit_sha
            if declared.get("corpus_unit_sha256") != unit_sha:
                blocked.append("CORPUS_MISMATCH: declared corpus_unit_sha256 differs from this corpus")
            if health is not None and health.get("corpus_sha256") != unit_sha:
                blocked.append("SERVICE_CORPUS_MISMATCH: the running service serves another corpus")
        except (OSError, ValueError, KeyError, TypeError) as error:
            blocked.append("CORPUS_INVALID: " + str(error)[:120])
    if health is not None:
        observed["service_rules"] = health.get("rules")
        if health.get("rules") != rules:
            blocked.append(f"SERVICE_RULES_MISMATCH: service runs {health.get('rules')}, round froze {rules}")
    for fact, consequence in INDEPENDENCE.items():
        if declared.get(fact) is not True:
            discounts.append(consequence)
    label = "BLOCKED" if blocked else ("development" if discounts else "formal")
    return {"round": declared.get("round"), "label": label,
            "name_to_use": {"BLOCKED": None, "formal": "正式评测", "development": "开发对比"}[label],
            "blocked": blocked, "discounts": discounts, "frozen": {"rules": rules, "arms": arms}, "observed": observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round", type=Path, required=True, help="Declaration JSON written by the organiser")
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--native", action="store_true", help="Also check the running flow service on 8787")
    parser.add_argument("--output", type=Path, required=True, help="New JSON file under evidence/")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
    declared = json.loads(args.round.read_text("utf-8"))
    health = None
    if args.native:
        from frozen_flow_native import service_health
        try:
            health = service_health()
        except RuntimeError as error:
            parser.error(str(error))
    dirty = [line[3:] for line in git("status", "--porcelain", "--", "src", "scripts").splitlines()]
    result = evaluate(declared, commit=git("rev-parse", "--short", "HEAD"), dirty=dirty, questions=args.questions,
                      reference=args.reference, corpus=args.corpus, health=health)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(2 if result["label"] == "BLOCKED" else 0)


if __name__ == "__main__":
    main()

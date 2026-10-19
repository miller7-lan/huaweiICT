"""Freeze remote public materials and build an isolated, narrow topic experiment.

Policy answers and model generations are audit inputs only, never SFT targets.
No checkout, merge, network collection, model calls, or production writes occur.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/training/qwen-topic-smoke.json"
SNAPSHOT_PATHS = (
    "data/knowledge/candidate-catalog.json",
    "evaluation/knowledge/pilot-business-questions.json",
    "evaluation/knowledge/pilot-ai-reference-answers.json",
    "evidence/knowledge-update/retrieved-model-trial-integrity.json",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_rows(dataset: dict, config: dict) -> tuple[list, list]:
    reverse = {}
    for topic, categories in config["topics"].items():
        for category in categories:
            if category in reverse:
                raise ValueError("Category is mapped twice")
            reverse[category] = topic
    cases = dataset["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or set(ids) != set(config["families"]):
        raise ValueError("Question IDs must be unique and exactly match the frozen family map")
    diagnostic = set(config["diagnostic_families"])
    if not diagnostic <= set(config["families"].values()):
        raise ValueError("Unknown diagnostic family")
    train, dev = [], []
    for case in cases:
        question = case["question"]
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Empty question")
        family = config["families"][case["id"]]
        row = {
            "case_id": case["id"], "family": family, "question": question,
            "topic": reverse[case["category"]], "source_category": case["category"],
            "label_origin": "repository_machine_draft_category_coarsened_by_config",
            "human_review_completed": False,
        }
        (dev if family in diagnostic else train).append(row)
    if {r["family"] for r in train} & {r["family"] for r in dev}:
        raise ValueError("Related question family leaks across partitions")
    if {r["topic"] for r in train} != set(config["topics"]):
        raise ValueError("A training topic has no example")
    return train, dev


def prepare(config: dict, output: Path, public_report: Path | None = None) -> dict:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", config["source_ref"] + "^{commit}"], cwd=ROOT,
    ).decode().strip()
    frozen = {}
    blobs = {}
    for path in SNAPSHOT_PATHS:
        raw = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)
        blobs[path] = json.loads(raw)
        target = output / "source-snapshot" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        frozen[path] = {"sha256": digest(raw), "bytes": len(raw)}
    train, dev = build_rows(blobs[config["source_path"]], config)
    files = {}
    for name, rows in (("train", train), ("diagnostic", dev)):
        raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
        (output / f"{name}.jsonl").write_bytes(raw)
        files[name] = {"sha256": digest(raw), "rows": len(rows), "topics": dict(Counter(r["topic"] for r in rows))}
    write_json(output / "experiment.json", config)
    catalog = blobs[SNAPSHOT_PATHS[0]]
    trial = blobs[SNAPSHOT_PATHS[3]]
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit, "source_branch": config["source_ref"], "frozen_files": frozen,
        "experiment_sha256": digest((output / "experiment.json").read_bytes()),
        "catalog_counts": catalog["counts"], "catalog_contains_full_original_bodies": False,
        "prior_trial": {k: trial[k] for k in ("run_id", "actual_case_outputs", "program_pass_count", "ai_semantic_pass_count", "both_program_and_ai_pass_count")},
        "training_objective": "four-way question topic classification; no business answer learning",
        "dataset": files,
        "related_question_families_separated": True,
        "human_review_completed": False,
        "independent_test_set": False,
        "policy_answers_used_as_training_targets": False,
        "historical_model_outputs_used_as_training_targets": False,
        "original_materials_restored": False,
        "local_worktree_merged_or_reset": False,
        "limits": ["Only 30 pre-existing development questions; diagnostic results are not independent accuracy.",
                   "Fee and duration each have only one question; coarsened labels do not establish coverage.",
                   "No policy validity or applicability is learned or approved by this experiment."],
    }
    write_json(output / "manifest.json", report)
    if public_report:
        write_json(public_report, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/training/topic-smoke")
    args = parser.parse_args()
    # Reuse the project's Windows ACL helper without adding it to training dependencies.
    from private_runtime import ensure_private_directory
    ensure_private_directory(ROOT / ".runtime/training")
    result = prepare(json.loads(args.config.read_text(encoding="utf-8")), args.output,
                     ROOT / "evidence/qwen-training/preparation.json")
    print(json.dumps({"source_commit": result["source_commit"], "dataset": result["dataset"],
                      "objective": result["training_objective"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

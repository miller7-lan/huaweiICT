"""Write a count-only offline receipt, optionally split an existing private review ZIP.

No network or model use. Does not read credentials or change service/business state.
The existing anonymous review material is copied verbatim, with a new developer
review notice; original references, scores and method mappings are not changed.
"""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp import frozen_qa_flow_v3 as flow
from dual_track_review import REPORT, PRIVATE, score


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def review_batches(bundle):
    output = PRIVATE / "crossdoc-24"
    if output.exists():
        raise ValueError("EXISTING_PRIVATE_BATCHES_PRESERVED")
    with ZipFile(bundle) as archive:
        text = archive.read("review.md").decode("utf-8-sig").replace("\r", "")
        sheets = [name for name in archive.namelist() if name.startswith("sheet-") and name.endswith(".csv")]
        if len(sheets) != 1:
            raise ValueError("ONE_ANONYMOUS_SHEET_REQUIRED")
        rows = list(csv.DictReader(archive.read(sheets[0]).decode("utf-8-sig").splitlines()))
    sections = re.split(r"(?m)^## (X\d+)\s*$", text)
    blocks = [(sections[i], sections[i + 1]) for i in range(1, len(sections), 2)]
    if len(blocks) != 24 or len(rows) != 72:
        raise ValueError("EXPECTED_24_QUESTIONS_72_OUTPUTS")
    output.mkdir()
    counts = []
    notice = ("# 开发者人工审核：历史24题开发输出\n\n"
              "本轮由开发者本人先审，不冒称双人独立盲评。A/B/C仅为原匿名标签。"
              "先核参考与原文，再给每份输出：正确/部分正确/错误/无法判断；非正确附理由。"
              "同时核必答事实覆盖、引文支持、停止是否合理，不能以程序核验代替。"
              "历史参考疑义另记，禁止改原题与原输出。评分不是业务更新批准。\n\n")
    for start in range(0, len(blocks), 3):
        group = blocks[start:start + 3]
        outputs = sum(len(re.findall(r"(?m)^#### 输出 [A-Z]", block)) for _, block in group)
        if outputs != 9:
            raise ValueError("EXPECTED_NINE_OUTPUTS_PER_BATCH")
        path = output / f"batch-C{start // 3 + 1:02d}.md"
        path.write_text(notice + "".join(f"## {key}\n{body}" for key, body in group), "utf-8")
        counts.append({"batch": path.stem, "questions": [key for key, _ in group], "outputs": outputs,
                       "sha256": digest(path)})
    return {"bundle_sha256": digest(bundle), "questions": 24, "outputs": 72, "batches": counts,
            "ratings_completed": 0, "review_kind": "开发者人工审核", "method_key_included": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-xml", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--output", type=Path, default=REPORT / "offline-receipt.json")
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(REPORT.resolve()):
        parser.error("NEW_REPORT_RECEIPT_REQUIRED")
    tree = ET.parse(args.test_xml)
    suites = list(tree.getroot().iter("testsuite"))
    tests = {key: sum(int(s.get(key, "0")) for s in suites)
             for key in ("tests", "failures", "errors", "skipped")}
    with (REPORT / "human-review.csv").open(encoding="utf-8-sig", newline="") as handle:
        ratings = score(list(csv.DictReader(handle)), json.loads((REPORT / "review-manifest.json").read_text("utf-8")))
    historical = ROOT / "evidence/crossdoc-flow-v3/ba99e5f/formal-summary.json"
    summary = json.loads(historical.read_text("utf-8"))
    diagnostics = json.loads((historical.parent / "formal-diagnostics.json").read_text("utf-8"))
    flow_failures = {k.removeprefix("draft_failure:"): v for k, v in diagnostics["totals"]["flow_v3"].items()
                     if k.startswith("draft_failure:")}
    files = ["src/gov_mcp/frozen_qa_flow_v3.py", "src/gov_mcp/frozen_qa_flow_server.py",
             "scripts/dual_track_review.py", "scripts/audit_dual_track.py",
             "tests/test_dual_track_repair.py", "tests/test_dual_track_review.py"]
    result = {"phase": "offline_stage_1", "frozen_code": git("rev-parse", "fedefb9"),
              "reviewed_main": "37e318ca12b33f453f99ab68962ab370a297d3ff", "branch": git("branch", "--show-current"),
              "main_core_diff_from_frozen": git("diff", "--name-only", "fedefb9", "37e318c", "--", "src", "scripts", "tests", "config"),
              "changed_source_sha256": {name: digest(ROOT / name) for name in files},
              "tests": tests, "test_xml_sha256": digest(args.test_xml),
              "historical_24_summary_sha256": digest(historical),
              "historical_24_actual_requests": summary["model_calls"],
              "historical_v3_failure_occurrences": flow_failures,
              "failure_kinds_with_specific_hints": {k: k in flow.REPAIR_HINTS for k in flow_failures},
              "developer_review": ratings, "new_model_calls": 0, "uploads": 0,
              "formal_state_changes": 0, "business_quality_measured": False,
              "merge_recommendation": "NOT_YET_VALIDATED",
              "note": "Tests use synthetic candidates; no real model improvement or human approval is inferred."}
    if args.bundle:
        result["historical_crossdoc_review_batches"] = review_batches(args.bundle)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"receipt": str(args.output), "tests": tests,
                      "developer_reviews_completed": ratings["completed"], "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Read-only, non-scoring check before an independent reviewer freezes references."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_eval_intake import inspect_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--pack", type=Path, default=ROOT / "data/real-matter-closure/nanjing/update-snapshots-v1/registered-four")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.resolve() == args.submission.resolve() or args.receipt.exists():
        parser.error("Input and previous receipts are never overwritten")
    raw = args.submission.read_bytes()
    if len(raw) > 1024 * 1024: parser.error("Submission exceeds 1 MiB")
    value = json.loads(raw)
    report = inspect_submission(value, args.pack, args.expected_commit)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("xb") as output: output.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "TECHNICAL_BINDING_READY_ATTRIBUTION_AND_FREEZE_STILL_REQUIRED" else 2


if __name__ == "__main__": raise SystemExit(main())

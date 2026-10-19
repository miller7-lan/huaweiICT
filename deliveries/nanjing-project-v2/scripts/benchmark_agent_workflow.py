"""Repeatable synthetic timings; never calls model providers or mutates user runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time

from gov_mcp.decision.workflow import Workflow

FACTS = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = [*sorted((root/"src/gov_mcp/decision").glob("*.py")), root/"config/decision/synthetic.snapshot.json", Path(__file__).resolve()]
    results = {"scope": "local synthetic fixture, not real-model or business accuracy", "remote_calls": 0, "timings": {},
               "python": platform.python_version(), "warmup_per_operation": 1,
               "source_sha256": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with tempfile.TemporaryDirectory() as directory:
        workflow = Workflow(Path(directory))
        operations = {
            "query": (20, lambda: workflow.query(FACTS, "branch-adult-self")),
            "branches": (40, lambda: workflow.branches(FACTS)),
            "search": (100, lambda: workflow.search_documents("申请旧卡交验")),
        }
        for name, (count, action) in operations.items():
            action()
            samples = []
            for _ in range(count):
                start = time.perf_counter()
                action()
                samples.append((time.perf_counter()-start)*1000)
            results["timings"][name] = {"iterations": count, "median_ms": round(statistics.median(samples), 3),
                                        "p95_ms": round(sorted(samples)[int(.95*(count-1))], 3)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()

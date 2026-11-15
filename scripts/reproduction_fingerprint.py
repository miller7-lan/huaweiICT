"""Deterministic fingerprint of one machine's setup, to compare two machines without a model call.

For a corpus and one or more questions-only files it records: the corpus unit hash, the
SHA-256 of the modules that decide gating, retrieval and checks, and for every question its
gate and the identity and text hash of each retrieved unit. Two machines on the same commit
and corpus must produce the same "fingerprint". The file holds ids and hashes, no question
or evidence text, so it can be committed.

With --records it also replays the recorded drafts through the current post-processing
(the same replay as export_flow_v3_diagnostics.py) and adds the per-record result kinds,
so the checks themselves are compared too.
"""
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow  # noqa: E402
from gov_mcp.frozen_retrieval import retrieve_with_plan  # noqa: E402

MODULES = ("frozen_qa.py", "frozen_retrieval.py", "frozen_qa_flow_v3.py", "frozen_qa_flow_server.py",
           "knowledge_retrieval.py")


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def question_rows(units, questions):
    rows = []
    for item in questions:
        query = item["question"] + ("\n" + item["context"] if item.get("context") else "")
        gate = frozen_qa.scope_gate(query)
        evidence = [] if gate else retrieve_with_plan(units, query)["evidence"]
        rows.append({"id": item["id"], "gate": gate,
                     "evidence": [[unit["source_id"], unit["locator"], unit["text_sha256"]] for unit in evidence],
                     "column_guide_sha256": sha256(flow.column_guide(evidence).encode()).hexdigest()})
    return rows


def replay_rows(units, records):
    import export_flow_v3_diagnostics as export
    corpus = {(unit["source_id"], unit["locator"]): unit for unit in units}
    rows = []
    for record in records:
        replayed = export.replay(record, corpus) or {}
        rows.append({"id": record.get("question_id"), "method": record.get("compared_as") or record.get("method"),
                     "replayed": replayed.get("result_kind"), "excerpted": replayed.get("claims_excerpted"),
                     "dropped": replayed.get("claims_dropped")})
    return sorted(rows, key=lambda row: (str(row["id"]), str(row["method"])))


def fingerprint(units, question_sets, records=None):
    modules = {name: sha256((ROOT / "src/gov_mcp" / name).read_bytes()).hexdigest() for name in MODULES}
    result = {"corpus_units": len(units),
              "corpus_unit_sha256": frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True)),
              "modules": modules, "question_sets": {}}
    for name, questions in question_sets.items():
        rows = question_rows(units, questions)
        result["question_sets"][name] = {"questions": len(rows), "sha256": digest(rows), "rows": rows}
    if records is not None:
        rows = replay_rows(units, records)
        result["replay"] = {"records": len(rows), "sha256": digest(rows), "rows": rows}
    result["fingerprint"] = digest({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def compare(first, second):
    """Names of the parts that differ; empty when the two machines agree."""
    parts = ["corpus_unit_sha256", "modules"]
    differ = [part for part in parts if first.get(part) != second.get(part)]
    for name in sorted(set(first["question_sets"]) | set(second["question_sets"])):
        a, b = first["question_sets"].get(name), second["question_sets"].get(name)
        if not a or not b or a["sha256"] != b["sha256"]:
            differ.append("question_set:" + name)
            if a and b:
                differ += [f"question:{name}:{x['id']}" for x, y in zip(a["rows"], b["rows"]) if x != y]
    if (first.get("replay") or {}).get("sha256") != (second.get("replay") or {}).get("sha256"):
        differ.append("replay")
    return differ


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make")
    make.add_argument("--corpus", type=Path, required=True)
    make.add_argument("--questions", type=Path, nargs="+", required=True)
    make.add_argument("--records", type=Path, help="Optional private records.jsonl to replay")
    make.add_argument("--output", type=Path, required=True, help="New JSON file under evidence/")
    diff = sub.add_parser("compare")
    diff.add_argument("first", type=Path)
    diff.add_argument("second", type=Path)
    args = parser.parse_args()
    if args.command == "compare":
        first, second = (json.loads(path.read_text("utf-8")) for path in (args.first, args.second))
        differ = compare(first, second)
        print(json.dumps({"same": not differ, "differ": differ}, ensure_ascii=False, indent=1))
        sys.exit(0 if not differ else 1)
    if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
    units = json.loads(args.corpus.read_text("utf-8"))["units"]
    sets = {}
    for path in args.questions:
        loaded = json.loads(path.read_text("utf-8"))
        cases = loaded["cases"] if isinstance(loaded, dict) else loaded
        # Probe files carry expected locators; only the question fields are used here.
        sets[path.stem] = frozen_qa.validate_questions(
            [{key: case[key] for key in ("id", "question", "context") if key in case} for case in cases])
    records = None
    if args.records:
        records = [json.loads(line) for line in args.records.read_text("utf-8").splitlines() if line.strip()]
    result = fingerprint(units, sets, records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({"fingerprint": result["fingerprint"], "corpus_unit_sha256": result["corpus_unit_sha256"],
                      "question_sets": {k: v["sha256"] for k, v in result["question_sets"].items()},
                      "replay": (result.get("replay") or {}).get("sha256")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

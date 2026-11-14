"""Turn private paired-run records into a text-free diagnostic file that can be committed.

Reads records.jsonl written by compare_frozen_flow_v3.py and the corpus it used,
rebuilds each record's evidence from the corpus and re-checks every claim of
the last model draft on its own. Nothing calls a model.

The output holds question ids, result kinds, call counts, failure-code kinds
and per-claim states (citation valid or not, lengths, kind of the cited unit).
It also replays each last draft through the current post-processing (backend
source citations, checks, salvage, excerpts) to show what would be displayed
with no further model call. It holds no question, answer, claim, quote or evidence text. Failure codes
lose their values unless --with-values is given; values are numbers, units and
dates taken from model drafts, so look at them before committing.
"""
import argparse
import json
import re
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gov_mcp import frozen_qa_flow_v3 as flow  # noqa: E402
from gov_mcp.frozen_qa import compact, normalize_citation_locators  # noqa: E402


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def kinds(failures, with_values):
    return [code if with_values else re.sub(r"^A\d+_", "", code).split(":")[0] for code in failures]


def rebuild_pool(record, corpus):
    return [dict(corpus[key], rank=item["rank"]) for item in record["retrieval"]["evidence"]
            if (key := (item["source_id"], item["locator"])) in corpus]


def replay(record, corpus):
    """What the current code would display from the recorded last draft, with no model call."""
    draft = record.get("model_output")
    if not draft:
        return None
    settled = flow.settle_draft(draft, rebuild_pool(record, corpus), list(corpus.values()))
    final = settled["final"]
    return {"result_kind": settled["result_kind"], "source_citations_used": settled["source_citations_used"],
            "claims_dropped": settled["claims_dropped"], "claims_excerpted": settled["claims_excerpted"],
            "final_checks_passed": settled.get("final_checks_passed"),
            "final_assertions": len(final["assertions"]), "final_unknowns": len(final["unknowns"]),
            "excerpt_chars": [len(item["quote"]) for item in final["assertions"]
                              if item["claim"].startswith(flow.EXCERPT_NOTE)]}


def claim_rows(record, corpus, with_values):
    """Re-check each claim of the last draft alone, with the backend source citations."""
    draft = record.get("model_output")
    if not draft or draft["outcome"] != "answer":
        return []
    pool = rebuild_pool(record, corpus)
    by_key = {(u["source_id"], u["locator"]): u for u in pool}
    draft = normalize_citation_locators(flow.clean(draft), pool)
    rows = []
    for index, item in enumerate(draft["assertions"], 1):
        unit = by_key.get((item["source_id"], item["locator"]))
        state = ("not_retrieved" if unit is None else
                 "quote_not_in_source" if not compact(item["quote"]) or compact(item["quote"]) not in compact(unit["text"])
                 else "valid")
        alone = {**draft, "answer": item["claim"], "assertions": [item]}
        supported = flow.attach_source_titles(alone, pool, list(corpus.values()))
        failures = flow.check(alone, pool)["failures"]
        rows.append({"claim": index, "citation": state, "unit_kind": unit.get("evidence_kind") if unit else None,
                     "claim_chars": len(item["claim"]), "quote_chars": len(item["quote"]),
                     "unit_chars": len(unit["text"]) if unit else None,
                     "fails_alone": kinds(failures, with_values),
                     "fails_with_source_citations": kinds(flow.check(supported, pool)["failures"], with_values)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file under evidence/")
    parser.add_argument("--with-values", action="store_true")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "evidence").resolve()) or args.output.exists():
        parser.error("NEW_FILE_UNDER_EVIDENCE_REQUIRED")
    corpus = {(u["source_id"], u["locator"]): u for u in json.loads(args.corpus.read_text("utf-8"))["units"]}
    rows, totals = [], {}
    for line in args.records.read_text("utf-8").splitlines():
        record = json.loads(line)
        name = record.get("compared_as") or record.get("method")
        final, calls = record["final"], record["model"].get("calls")
        if calls is None:  # single-shot record
            calls = [{"outcome": (record.get("model_output") or {}).get("outcome"),
                      "failures": (record.get("checks") or {}).get("failures", [])}] if record["model"].get("called") else []
        claims = claim_rows(record, corpus, args.with_values)
        row = {"question_id": record.get("question_id"), "compared_as": name,
               "result_kind": record.get("result_kind") or final["outcome"],
               "stop_origin": record.get("stop_origin") or record.get("gate") or (
                   final["stop_reason"] if final["outcome"] == "stop" and record.get("checks") and
                   not record["checks"]["passed"] else None),
               "model_error": bool(record["model"].get("error")),
               "calls": [{"outcome": call.get("outcome"), "pool_size": call.get("pool_size"),
                          "assertions": call.get("assertions"), "unknowns": call.get("unknowns"),
                          "failures": kinds(call.get("failures", []), args.with_values)} for call in calls],
               "follow_ups_used": record.get("follow_ups_used"), "repairs_used": record.get("repairs_used"),
               "claims_dropped": record.get("claims_dropped"), "claims_excerpted": record.get("claims_excerpted"),
               "retrieval_rounds": [{"kind": r.get("kind"), "added": r.get("added")}
                                    for r in record["retrieval"].get("rounds", [])],
               "evidence_units": len(record["retrieval"]["evidence"]),
               "final_assertions": len(final["assertions"]),
               "final_sources": len({a["source_id"] for a in final["assertions"]}),
               "final_unknowns": len(final["unknowns"]),
               "final_checks_passed": (record.get("final_checks") or {}).get("passed"),
               "last_draft_claims": claims, "last_draft_replayed": replay(record, corpus)}
        rows.append(row)
        counts = totals.setdefault(name, Counter())
        counts["result:" + row["result_kind"]] += 1
        counts["model_calls"] += len(calls)
        for call in calls:
            for code in call.get("failures", []):
                counts["draft_failure:" + re.sub(r"^A\d+_", "", code).split(":")[0]] += 1
        if row["last_draft_replayed"]:
            counts["replay:" + row["last_draft_replayed"]["result_kind"]] += 1
            counts["replay_claims_excerpted"] += row["last_draft_replayed"]["claims_excerpted"]
            counts["replay_claims_dropped"] += row["last_draft_replayed"]["claims_dropped"]
        for claim in claims:
            counts["claim_citation:" + claim["citation"]] += 1
            if claim["fails_alone"]:
                counts["claim_fails_alone"] += 1
                counts["claim_fixed_by_source_citations"] += not claim["fails_with_source_citations"]
    result = {"scope": "ids, counts and failure-code kinds only; no question, answer, claim, quote or evidence text",
              "with_values": args.with_values, "records_sha256": file_sha(args.records),
              "corpus_sha256": file_sha(args.corpus),
              "flow_sha256": file_sha(ROOT / "src/gov_mcp/frozen_qa_flow_v3.py"),
              "totals": {name: dict(sorted(counts.items())) for name, counts in totals.items()}, "records": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({"output": str(args.output), "records": len(rows), "totals": result["totals"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

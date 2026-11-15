"""Offline comparison of verification designs for flow V3 on synthetic cases (no model call).

  python run_bench.py --src SRC --candidate P0|A|C|E     one candidate, JSON to stdout
  python run_bench.py --all --baseline-src OLD_SRC       every candidate, table and JSON

Candidates (see the report next to this file):
  P0  fedefb9 flow V3 as frozen (point --src at a checkout of that commit)
  A   clause binding: claim_binding checks inside flow V3, answer rendered from claims only
  C   card binding: as A, but quote-side facts come from scripts/extract_fact_cards.py cards
  E   extract-only display: any valid quote is shown as its whole source sentence; claims hidden

Each draft goes through the candidate's zero-call settle step, the same step used to replay
stored drafts. Scores count displayed text only. They are synthetic regression signals for
choosing a design, not accuracy on real questions.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(src, candidate):
    sys.path.insert(0, str(src))
    sys.path.insert(0, str(HERE))
    from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow
    gate = getattr(flow, "scope_gate", frozen_qa.scope_gate)
    if candidate == "C":
        sys.path.insert(0, str(ROOT / "scripts"))
        flow.binder = card_binder
    if candidate == "F":
        flow.binder = novelty_binder
    return frozen_qa, flow, gate


RISKY = set("不免非无未均都所全任")


def novelty_binder(index, claim, quote, unit):
    """F: A, plus a claim may not add CJK bigrams with a negating or universal character that its
    quote does not contain (extractive-fragment coverage, after Grusky et al. 2018)."""
    from gov_mcp import claim_binding as cb
    failures = cb.binding_failures(index, claim, quote)

    def bigrams(text):
        runs = re.findall(r"[\u4e00-\u9fff]+", text)
        return {run[i:i + 2] for run in runs for i in range(len(run) - 1)}
    novel = bigrams(claim) - bigrams(unit["text"])
    if any(RISKY & set(gram) for gram in novel):
        failures.append(f"A{index}_NOVEL_NEGATING_WORDING")
    return failures


def card_binder(index, claim, quote, unit):
    """Clause binding with quote-side facts taken from fact cards inside the quote span."""
    from gov_mcp import claim_binding as cb
    import extract_fact_cards as cards_module
    failures = []
    span = cb.find_loose(unit["text"], quote) or (0, len(unit["text"]))
    cards = [c for c in cards_module.unit_cards(unit) if c["type"] in ("amount", "duration", "count")
             and span[0] <= c["span"][0] and c["span"][1] <= span[1]]
    facts = []
    for card in cards:
        value = cb.number_value(card["value"]["number"])
        unit_name = card["value"]["unit"].lstrip("个")
        branches = cb.ids(cb.BRANCH, cb.BRANCH_ID, "、".join(card["subjects"]))
        facts.append({"q": (value, unit_name), "branches": branches,
                      "attributes": cb.ids(cb.ATTRIBUTE, cb.ATTRIBUTE_ID, card["sentence"])})
    claim_branches_all = cb.ids(cb.BRANCH, cb.BRANCH_ID, claim)
    for start, end in cb.clause_spans(claim):
        part = claim[start:end]
        branches = cb.ids(cb.BRANCH, cb.BRANCH_ID, part) or (claim_branches_all if len(claim_branches_all) == 1 else set())
        attributes = cb.ids(cb.ATTRIBUTE, cb.ATTRIBUTE_ID, part)
        for q in sorted(cb.quantities(part)):
            holding = [f for f in facts if f["q"] == q]
            if not holding:
                failures.append(f"A{index}_QUANTITY_NOT_IN_CARDS:{q[0]}{q[1]}")
            elif all(cb.conflicts(branches, f["branches"]) for f in holding):
                failures.append(f"A{index}_BRANCH_MISMATCH:{q[0]}{q[1]}")
            elif all(cb.attribute_conflict(attributes, f["attributes"], q[1]) for f in holding):
                failures.append(f"A{index}_ATTRIBUTE_MISMATCH:{q[0]}{q[1]}")
    # Restriction, generalization and negation are sentence-level; cards carry no more than A here.
    failures.extend(code for code in cb.binding_failures(index, claim, quote)
                    if not re.search(r"QUANTITY_NOT_IN_OWN_QUOTE|BRANCH_MISMATCH|ATTRIBUTE_MISMATCH", code))
    return failures


def settle_extract_only(flow, frozen_qa, raw, pool):
    """E: claims select spans only; the display is the whole source sentence of each valid quote."""
    output = frozen_qa.normalize_citation_locators(flow.clean(raw), pool)
    units = {(u["source_id"], u["locator"]): u for u in pool}
    shown = []
    if output["outcome"] == "answer":
        for item in output["assertions"]:
            unit = units.get((item["source_id"], item["locator"]))
            if unit is None or unit.get("evidence_kind") in frozen_qa.METADATA_KINDS:
                continue
            if not frozen_qa.compact(item["quote"]) or frozen_qa.compact(item["quote"]) not in frozen_qa.compact(unit["text"]):
                continue
            text = flow.sentence_around(unit["text"], item["quote"])
            if text and text not in shown:
                shown.append(text)
    if not shown:
        return {"result_kind": "stop", "final": frozen_qa._stop("NO_VALID_QUOTE", "没有可展示的归档原文。")}
    return {"result_kind": "answer", "final": {"outcome": "answer", "answer": "\n".join("- 原文：" + t for t in shown),
                                               "assertions": [], "stop_reason": "", "unknowns": []}}


def displayed(final, flow):
    parts = [final.get("answer", ""), final.get("stop_reason", ""), *final.get("unknowns", [])]
    parts += [item["claim"] for item in final.get("assertions", []) if not item["claim"].startswith(flow.TITLE_NOTE)]
    return re.sub(r"\s+", "", "\n".join(parts))


def run(src, candidate):
    frozen_qa, flow, gate = load(src, candidate)
    from cases import DRAFT_CASES, GAP_CASES, GATE_CASES, UNITS
    from perturb import generated
    import holdout
    auto_units, auto_cases = generated()
    corpus = UNITS + auto_units + holdout.UNITS
    pool = [dict(u, rank=i, score=1) for i, u in enumerate(corpus, 1)]
    started = time.monotonic()
    rows = []
    for case in DRAFT_CASES + auto_cases + holdout.CASES:
        if candidate == "E":
            settled = settle_extract_only(flow, frozen_qa, case["draft"], pool)
        else:
            settled = flow.settle_draft(case["draft"], pool, corpus)
        kind, text = settled["result_kind"], displayed(settled["final"], flow)
        if case["kind"] == "bad":
            ok = not any(re.sub(r"\s+", "", s) in text for s in case["must_not_show"])
            direct = None
        else:
            ok = kind != "stop"
            direct = ok and all(re.sub(r"\s+", "", s) in text for s in case["must_show"])
        rows.append({"id": case["id"], "issue": case["issue"], "kind": case["kind"], "result_kind": kind,
                     "ok": ok, "direct": direct})
    for case in GAP_CASES:
        settled = (settle_extract_only(flow, frozen_qa, case["draft"], pool) if candidate == "E"
                   else flow.settle_draft(case["draft"], pool, corpus))
        rows.append({"id": case["id"], "issue": case["issue"], "kind": "label", "result_kind": settled["result_kind"],
                     "ok": settled["result_kind"] == case["expect_kind"], "direct": None})
    for case_id, issue, question, expected in GATE_CASES:
        got = gate(question)
        rows.append({"id": case_id, "issue": issue, "kind": "gate", "result_kind": got, "ok": got == expected,
                     "direct": None})
    elapsed = time.monotonic() - started
    digest = sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"candidate": candidate, "rows": rows, "elapsed_s": round(elapsed, 3), "rows_sha256": digest}


def summarize(result):
    rows = result["rows"]

    def rate(selected):
        return f"{sum(r['ok'] for r in selected)}/{len(selected)}"

    good = [r for r in rows if r["kind"] == "good"]
    return {
        "bad_blocked_dev": rate([r for r in rows if r["kind"] == "bad" and r["issue"] != "holdout"]),
        "bad_blocked_audit": rate([r for r in rows if r["kind"] == "bad" and r["issue"] not in ("holdout",)
                                   and not r["issue"].startswith("auto")]),
        "bad_blocked_auto": rate([r for r in rows if r["kind"] == "bad" and r["issue"].startswith("auto")]),
        "holdout_bad_blocked": rate([r for r in rows if r["kind"] == "bad" and r["issue"] == "holdout"]),
        "holdout_good_direct": f"{sum(bool(r['direct']) for r in good if r['issue'] == 'holdout')}/"
                               f"{sum(r['issue'] == 'holdout' for r in good)}",
        "good_direct_dev": f"{sum(bool(r['direct']) for r in good if r['issue'] != 'holdout')}/"
                           f"{sum(r['issue'] != 'holdout' for r in good)}",
        "gap_label": rate([r for r in rows if r["kind"] == "label"]),
        "gate": rate([r for r in rows if r["kind"] == "gate"]),
        "failed_ids": [r["id"] for r in rows if not r["ok"]],
        "elapsed_s": result["elapsed_s"],
    }


def run_tests(src, candidate):
    """Existing flow tests against the candidate's source tree (C patches the binder in)."""
    tests = ["tests/test_frozen_qa.py", "tests/test_frozen_qa_flow_v3.py", "tests/test_frozen_qa_flow_server.py",
             "tests/test_frozen_flow_native.py", "tests/test_crossdoc_native_wiring.py"]
    env_code = ""
    if candidate in ("C", "F"):
        hook = "card_binder" if candidate == "C" else "novelty_binder"
        env_code = (f"import sys; sys.path[:0]=[{str(HERE)!r},{str(ROOT / 'scripts')!r}];"
                    f"import run_bench; from gov_mcp import frozen_qa_flow_v3 as f; f.binder=run_bench.{hook};")
    tree = Path(src).parent
    code = env_code + f"import sys,pytest; sys.exit(pytest.main(['-q','-p','no:cacheprovider',*{tests!r}]))"
    proc = subprocess.run([sys.executable, "-c", code], cwd=tree, capture_output=True, text=True,
                          env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
    return tail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=ROOT / "src")
    parser.add_argument("--candidate", choices=("P0", "A", "C", "E", "F"))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--baseline-src", type=Path, help="src/ of a fedefb9 checkout, for P0")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.all:
        print(json.dumps(run(args.src, args.candidate), ensure_ascii=False))
        return
    report = {}
    for candidate in ("P0", "A", "C", "E", "F"):
        src = args.baseline_src if candidate == "P0" else args.src
        runs = []
        for _ in range(2):  # determinism: two runs in fresh processes must give the same rows
            proc = subprocess.run([sys.executable, __file__, "--src", str(src), "--candidate", candidate],
                                  capture_output=True, text=True, check=True)
            runs.append(json.loads(proc.stdout))
        summary = summarize(runs[0])
        summary["deterministic"] = runs[0]["rows_sha256"] == runs[1]["rows_sha256"]
        summary["existing_tests"] = run_tests(src, "P0" if candidate == "P0" else candidate)
        report[candidate] = {"summary": summary, "rows": runs[0]["rows"]}
        print(candidate, json.dumps({k: v for k, v in summary.items() if k != "failed_ids"}, ensure_ascii=False))
        print("   failed:", ", ".join(summary["failed_ids"]) or "none")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

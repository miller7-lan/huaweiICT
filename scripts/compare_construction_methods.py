"""Compare machine-side ontology candidate construction methods on the six low-resource units.

The run is isolated: it reads the frozen corpus, the unit definitions and the
seed ontology, calls the configured cloud model, and writes raw candidates to a
new private runtime directory. Nexent, knowledge bases and ontology workspaces
are not touched. Candidate text stays private until both method-A records are
returned, so the public summary carries counts, rates and hashes only.

Every method passes through the same deterministic gates (exact quote, numbers
traceable to the quote, de-duplication). Recall is measured against elements
extracted mechanically from the source text, plus value/branch bindings for
U4-U6 that were confirmed in development (appendix 3.3.5 and 3.4). Human review
minutes are not measured here and must not be inferred from these numbers.

Without --live this only evaluates the stored 11-05 method-B record offline.
"""
import argparse
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from generate_unit_candidates import CORPUS, OUTPUT as STORED_METHOD_B, ROOT, UNITS, CandidateOutput, compact, resolve
from generate_unit_candidates import messages as method_b_messages
from gov_mcp.json_output import StrictOutput

SEED = ROOT / "config/preparation/ontology.seed.json"
RECEIPT = ROOT / "evidence/lowresource-20261105/method-b-candidates-receipt.json"
PRIVATE = ROOT / ".runtime/lowresource-architecture"
PUBLIC = ROOT / "evidence/lowresource-20261105"
SEED_RELATIONS = {r["name"] for r in json.loads(SEED.read_text("utf-8"))["relation_types"]}
REPEATS = 3
VOTE_SAMPLES = 3
VOTE_TEMPERATURE = 0.7
METHODS = {
    "B0": "通用三元组抽取：无单元目标、无种子本体、不要求逐字引文",
    "B1": "现行方法B：单元目标加逐字引文，单次调用",
    "B2": "种子本体约束：B1加种子类型与关系、一事实一条、单位不补，单次调用",
    "B3": "B2三次采样（温度0.7）多数投票，少于两票的候选标为低置信",
}
_CN = {**dict(zip("零一二三四五六七八九", range(10))), "两": 2}
_ISSUERS = "财综|财税|发改价格|豫公通|公通字"
_BRANCHES = ("首次申领", "申领", "换领", "补领", "遗失补领", "损坏换领", "临时居民身份证", "绿色通道", "监护人", "省内通办")
# Facts already confirmed in development and published in the appendix. A fact
# is bound when one accepted candidate carries the value and the required
# branch and none of the forbidden text; otherwise that candidate misbinds it.
BINDINGS = {
    "U4": [(r"(?<!\d)20元", r"首次申领|(?<!损坏)换领", None),
           (r"(?<!\d)40元", r"遗失补领|损坏换领", None),
           (r"(?<!\d)10元", r"临时", None)],
    "U5": [(r"(?<!\d)25(?:自然日|工作日|日)", None, r"绿色通道|考生|25工作日"),
           (r"(?<!\d)15工作日", None, r"绿色通道|考生"),
           (r"(?<!\d)15自然日", r"绿色通道|考生", None),
           (r"(?<!\d)5工作日", r"绿色通道|考生", None)],
    "U6": [(r"(?<!\d)40(?:自然日|工作日|日)", r"自然日", r"40工作日"),
           (r"(?<!\d)30(?:自然日|工作日|日)", None, r"30(?:自然日|工作日)")],
}


class Triple(StrictOutput):
    subject: str
    predicate: str
    object: str
    evidence: str


class TripleOutput(StrictOutput):
    triples: list[Triple]


def _cn(text):
    value = current = 0
    for char in text:
        if char in "百十":
            value += (current or 1) * (100 if char == "百" else 10)
            current = 0
        else:
            current = _CN[char]
    return value + current


def norm(text):
    """Whitespace-free text with dates and Chinese counts written as Arabic numbers."""
    text = compact(text)
    text = re.sub(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", lambda m: f"{m[1]}-{int(m[2])}-{int(m[3])}", text)
    text = re.sub(r"[零一二两三四五六七八九十百]+(?=个?(?:周岁|自然日|工作日|月|日|元|条|年|枚|项))",
                  lambda m: str(_cn(m[0])), text)
    return re.sub(r"个(?=月|自然日|工作日)", "", text)


def elements(texts):
    """Dates, amounts, durations, document numbers, titles and branch terms in the source."""
    found = set()
    for item in texts:
        text = norm(item["text"])
        for pattern in (r"\d{4}-\d{1,2}-\d{1,2}", r"\d{4}年\d{1,2}月底", rf"(?:{_ISSUERS})〔\d{{4}}〕\d+号",
                        r"《[^》]{2,40}》", r"\d{4}年", r"\d+(?:周岁|自然日|工作日|月|日|元)"):
            found.update(match.strip("《》") for match in re.findall(pattern, text))
            text = re.sub(pattern, "\x1f", text)
        found.update(term for term in _BRANCHES if term in norm(item["text"]))
    return sorted(found)


def fields(candidate):
    return norm("|".join([candidate["subject"], candidate["predicate"], candidate["object"], *candidate["qualifiers"]]))


def gate(candidates, texts):
    """Mark each candidate; accepted ones are exact, traceable and not duplicated."""
    sources = [compact(item["text"]) for item in texts]
    locators = set(re.findall(r"\d+", norm(" ".join(item["locator"] + item["source_id"] for item in texts))))
    seen, marked = set(), []
    for candidate in candidates:
        quote = compact(candidate["quote"])
        exact = bool(quote) and any(quote in source for source in sources)
        traceable = set(re.findall(r"\d+", fields(candidate))) <= set(re.findall(r"\d+", norm(quote))) | locators
        key = "|".join(norm(candidate[name]) for name in ("subject", "predicate", "object"))
        duplicate = key in seen
        seen.add(key)
        marked.append({**candidate, "quote_exact": exact, "numbers_traceable": traceable, "duplicate": duplicate,
                       "accepted": exact and traceable and not duplicate})
    return marked


def bindings(unit_id, accepted):
    bound, misbound = 0, 0
    for value, required, forbidden in BINDINGS.get(unit_id, []):
        hits = [fields(c) for c in accepted if re.search(value, fields(c))]
        good = [h for h in hits if (not required or re.search(required, h)) and not (forbidden and re.search(forbidden, h))]
        bound += bool(good)
        misbound += len(hits) - len(good)
    return bound, misbound


def signature(candidate, unit_elements):
    captured = tuple(sorted(e for e in unit_elements if norm(e) in fields(candidate)))
    return norm(candidate["predicate"]), captured or norm(candidate["object"])


def vote(samples, unit_elements):
    """Keep accepted candidates whose signature appears in at least two samples."""
    counts = {}
    for sample in samples:
        for key in {signature(c, unit_elements) for c in sample}:
            counts[key] = counts.get(key, 0) + 1
    kept, low, used = [], [], set()
    for sample in samples:
        for candidate in sample:
            key = signature(candidate, unit_elements)
            if key in used:
                continue
            used.add(key)
            (kept if counts[key] >= 2 else low).append(candidate)
    return kept, low


def seed_prompt(unit, texts, seed):
    base = method_b_messages(unit, texts)
    entities = "、".join(f"{t['name']}（{t['label']}）" for t in seed["entity_types"])
    relations = "、".join(f"{r['name']}（{r['domain']}→{r['range']}）" for r in seed["relation_types"])
    base[0]["content"] += (
        f"\n使用种子本体。实体类型：{entities}。关系：{relations}。优先使用种子关系名；种子不足时自拟小写下划线英文扩展关系名，"
        "并在 qualifiers 中写“扩展关系”。每条候选只表达一个事实：一个分支、一个版本取值、一个条件或一个依据，"
        "不同分支、不同版本不要合并成一条。数值连同单位原样写入 object 或 qualifiers；原文没有写明单位或日类型的写“未注明”，不要补全。"
        "条件结果只取 true/false/unknown。quote 取能支持该事实的最短连续原文，一般不超过80字。")
    return base


def generic_prompt(texts):
    evidence = "\n\n".join(f"[{item['source_id']} | {item['locator']}]\n{item['text']}" for item in texts)
    return [{"role": "system", "content": "你是知识图谱抽取助手。"},
            {"role": "user", "content": f"从下列政务原文中抽取知识图谱三元组，每条给出主体、关系、客体和依据原文。\n\n原文：\n{evidence}"}]


def jobs(plan):
    for method in METHODS:
        for repeat in range(1, REPEATS + 1):
            for unit in plan["units"]:
                for sample in range(1, (VOTE_SAMPLES if method == "B3" else 1) + 1):
                    yield {"method": method, "repeat": repeat, "sample": sample, "unit": unit["id"]}


def call(job, unit, texts, seed):
    from gov_mcp.deepseek import PROFILES, chat
    if job["method"] == "B0":
        args = generic_prompt(texts), TripleOutput, 0.0
    elif job["method"] == "B1":
        args = method_b_messages(unit, texts), CandidateOutput, 0.0
    else:
        args = seed_prompt(unit, texts, seed), CandidateOutput, VOTE_TEMPERATURE if job["method"] == "B3" else 0.0
    started = time.perf_counter()
    try:
        response = chat(PROFILES["official"], args[0], response_model=args[1], max_output_tokens=3072, temperature=args[2])
    except Exception as error:
        return {**job, "status": "error", "error": str(error)[:200], "latency_s": round(time.perf_counter() - started, 3)}
    output = response["output"]
    candidates = output["candidates"] if "candidates" in output else [
        {"kind": "triple", "subject": t["subject"], "predicate": t["predicate"], "object": t["object"],
         "qualifiers": [], "quote": t["evidence"]} for t in output["triples"]]
    return {**job, "status": "ok", "model": response["model"], "usage": response["usage"],
            "latency_s": round(time.perf_counter() - started, 3), "candidates": candidates}


def jaccard(sets):
    pairs = list(combinations(sets, 2))
    return round(sum(len(a & b) / len(a | b) if a | b else 1.0 for a, b in pairs) / len(pairs), 3) if pairs else None


def evaluate(method, records, resolved, unit_elements):
    """Per-unit metrics averaged over repeats; calls with errors keep their denominator."""
    summary = {}
    for unit_id, texts in resolved.items():
        runs, captured_sets = [], []
        repeats = sorted({r["repeat"] for r in records if r["unit"] == unit_id})
        for repeat in repeats:
            calls = [r for r in records if r["unit"] == unit_id and r["repeat"] == repeat]
            samples = [gate(r["candidates"], texts) for r in calls if r["status"] == "ok"]
            raw = [c for sample in samples for c in sample]
            accepted_samples = [[c for c in sample if c["accepted"]] for sample in samples]
            if method == "B3":
                accepted, low = vote(accepted_samples + [[]] * (len(calls) - len(samples)), unit_elements[unit_id])
            else:
                accepted, low = (accepted_samples[0] if accepted_samples else []), []
            captured = {e for e in unit_elements[unit_id] if any(norm(e) in fields(c) for c in accepted)}
            captured_sets.append(captured)
            bound, misbound = bindings(unit_id, accepted)
            runs.append({
                "calls": len(calls), "errors": sum(r["status"] != "ok" for r in calls),
                "raw": len(raw), "accepted": len(accepted), "low_confidence": len(low),
                "quote_exact": sum(c["quote_exact"] for c in raw), "numbers_traceable": sum(c["numbers_traceable"] for c in raw),
                "duplicates": sum(c["duplicate"] for c in raw),
                "element_recall": len(captured) / len(unit_elements[unit_id]) if unit_elements[unit_id] else None,
                "bound": bound, "misbound": misbound,
                "seed_relation": sum(c["predicate"].strip() in SEED_RELATIONS for c in accepted),
                "machine_relation_name": sum(bool(re.fullmatch(r"[a-z][a-z0-9_]{1,40}", c["predicate"].strip())) for c in accepted),
                "quote_chars": sum(len(compact(c["quote"])) for c in accepted),
                "review_chars": sum(len(fields(c)) + len(compact(c["quote"])) for c in accepted),
                "input_tokens": sum(r.get("usage", {}).get("input_tokens", 0) for r in calls),
                "output_tokens": sum(r.get("usage", {}).get("output_tokens", 0) for r in calls),
                "latency_s": sum(r.get("latency_s", 0) for r in calls)})
        mean = {key: round(sum(run[key] for run in runs) / len(runs), 3) for key in runs[0] if key != "element_recall"}
        recalls = [run["element_recall"] for run in runs if run["element_recall"] is not None]
        summary[unit_id] = {**mean, "repeats": len(runs), "elements": len(unit_elements[unit_id]),
                            "binding_facts": len(BINDINGS.get(unit_id, [])),
                            "element_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
                            "element_recall_min": round(min(recalls), 3) if recalls else None,
                            "stability_jaccard": jaccard(captured_sets)}
    return summary


def totals(per_unit):
    units = list(per_unit.values())
    total = {key: round(sum(u[key] for u in units), 3) for key in (
        "calls", "errors", "raw", "accepted", "low_confidence", "quote_exact", "numbers_traceable", "duplicates",
        "bound", "misbound", "seed_relation", "machine_relation_name", "quote_chars", "review_chars",
        "input_tokens", "output_tokens", "latency_s", "binding_facts")}
    recalls = [u["element_recall"] for u in units if u["element_recall"] is not None]
    stability = [u["stability_jaccard"] for u in units if u["stability_jaccard"] is not None]
    accepted = total["accepted"] or None
    return {**total,
            "gate_pass_rate": round(total["accepted"] / total["raw"], 3) if total["raw"] else None,
            "quote_exact_rate": round(total["quote_exact"] / total["raw"], 3) if total["raw"] else None,
            "numbers_traceable_rate": round(total["numbers_traceable"] / total["raw"], 3) if total["raw"] else None,
            "element_recall_macro": round(sum(recalls) / len(recalls), 3) if recalls else None,
            "binding_recall": round(total["bound"] / total["binding_facts"], 3) if total["binding_facts"] else None,
            "seed_relation_rate": round(total["seed_relation"] / accepted, 3) if accepted else None,
            "machine_relation_name_rate": round(total["machine_relation_name"] / accepted, 3) if accepted else None,
            "mean_quote_chars": round(total["quote_chars"] / accepted, 1) if accepted else None,
            "stability_jaccard_macro": round(sum(stability) / len(stability), 3) if stability else None}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the cloud model for every planned job")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    plan, seed = json.loads(UNITS.read_text("utf-8")), json.loads(SEED.read_text("utf-8"))
    corpus = json.loads(CORPUS.read_text("utf-8"))["units"]
    resolved = {unit["id"]: resolve(unit, corpus) for unit in plan["units"]}
    unit_elements = {key: elements(texts) for key, texts in resolved.items()}
    receipt = json.loads(RECEIPT.read_text("utf-8"))
    if sha256(STORED_METHOD_B) != receipt["private_file_sha256"]:
        raise SystemExit("STORED_METHOD_B_RECORD_CHANGED")
    stored = json.loads(STORED_METHOD_B.read_text("utf-8"))["units"]
    stored_records = [{"method": "B1-stored", "repeat": 1, "sample": 1, "unit": key, "status": "ok",
                       "usage": value["usage"], "latency_s": 0, "candidates": value["candidates"]}
                      for key, value in stored.items()]
    results = {"B1-stored": evaluate("B1", stored_records, resolved, unit_elements)}
    planned = list(jobs(plan))
    if not args.live:
        print(json.dumps({"dry_run": True, "elements": {k: len(v) for k, v in unit_elements.items()},
                          "planned_model_calls": len(planned), "B1-stored": totals(results["B1-stored"])},
                         ensure_ascii=False, indent=1))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private = PRIVATE / stamp
    private.mkdir(parents=True)  # A fresh directory per run; earlier runs are never overwritten.
    units = {unit["id"]: unit for unit in plan["units"]}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(lambda job: call(job, units[job["unit"]], resolved[job["unit"]], seed), planned))
    wall = round(time.perf_counter() - started, 1)
    for method in METHODS:
        results[method] = evaluate(method, [r for r in records if r["method"] == method], resolved, unit_elements)
    raw_path = private / "raw.json"
    raw_path.write_text(json.dumps({"stamp": stamp, "records": records, "elements": unit_elements},
                                   ensure_ascii=False, indent=1), "utf-8")
    served = sorted({r["model"] for r in records if r["status"] == "ok"})
    public = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "stamp": stamp,
        "status": "machine_side_comparison_not_human_timing", "business_acceptance": False,
        "isolation": "frozen corpus and unit text only; no Nexent, knowledge base or ontology workspace access; raw candidates private",
        "methods": METHODS, "repeats": REPEATS, "vote_samples": VOTE_SAMPLES, "vote_temperature": VOTE_TEMPERATURE,
        "model": {"requested": "deepseek-flash", "served": served}, "wall_clock_s": wall,
        "model_calls": len(records), "errors": sum(r["status"] != "ok" for r in records),
        "inputs": {"units_sha256": sha256(UNITS), "corpus_sha256": sha256(CORPUS), "seed_sha256": sha256(SEED),
                   "script_sha256": sha256(__file__), "generator_sha256": sha256(Path(__file__).with_name("generate_unit_candidates.py")),
                   "stored_method_b_sha256": receipt["private_file_sha256"]},
        "private_raw": {"path": raw_path.relative_to(ROOT).as_posix(), "sha256": sha256(raw_path)},
        "elements_per_unit": {k: len(v) for k, v in unit_elements.items()},
        "seen_in_development": [u["id"] for u in plan["units"] if u["seen_in_development"]],
        "totals": {method: totals(per_unit) for method, per_unit in results.items()},
        "per_unit": results,
        "limits": ["不测人工主动分钟，不能换算为效率提升百分比",
                   "要素召回按原文机械抽取的日期、数值、文号、标题和分支词计算，不判断语义正确",
                   "U4—U6在开发中使用过，绑定事实来自开发期确认结果，结果可能偏乐观",
                   "候选正文在两人方法A交回前不公开"]}
    out = PUBLIC / f"architecture-comparison-{stamp}.json"
    out.write_text(json.dumps(public, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({"evidence": out.relative_to(ROOT).as_posix(), "calls": len(records), "errors": public["errors"],
                      "wall_clock_s": wall, "totals": public["totals"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

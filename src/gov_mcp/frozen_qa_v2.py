"""Cross-document free Q&A over the frozen corpus: plan, pool, answer, check, repair.

The first chain (frozen_qa.py) retrieves the top five units for the whole
question once, lets the model answer once, and turns any failed check or any
"not enough evidence" into a final stop. Questions that need two documents
lose there: one document fills the five slots, and nothing can be fetched or
corrected afterwards.

This chain keeps the same corpus, gates, model contract and status fields and
changes the flow:

1. plan     split the question into facets (no model call);
2. pool     retrieve per facet and merge round-robin with a per-source cap, so
            every facet and more than one document reach the model;
3. answer   one model call over the pooled evidence;
4. follow up  a first "not enough evidence" is a request, not a result: its
            stop_reason and unknowns become queries for one more retrieval;
5. check    every claim is checked against its own quote: numbers with their
            units, day units, and prohibition turned into permission;
6. repair   a failed check goes back to the model once, with the failure codes.

Evidence only grows within a task, and checks always read the whole pool.
Scope, privacy and current-validity gates still stop before any model call.
Bounds: at most MAX_MODEL_CALLS model calls per question. frozen_qa.py and its
recorded evaluations are not changed; results of this chain need their own
evaluation and are archive explanations kept PAUSED_UNREVIEWED.
"""
import json
import re
import time

from gov_mcp.frozen_qa import (DAY_UNITS, GATE_MESSAGES, NUMBER, REFERENCE, STATUS, build_messages, compact,
                               retrieve, scope_gate, sha, _stop)
from gov_mcp.knowledge_retrieval import VERSION as RETRIEVAL_VERSION


WORKFLOW = "crossdoc-v2"
PER_QUERY = 4          # candidates read from each facet's ranking
PER_SOURCE = 3         # units one source may hold in a single retrieval round
ROUND_SIZE = 8         # units added by one retrieval round
MAX_FACETS = 4
MAX_FOLLOW_UPS = 1
MAX_REPAIRS = 1
MAX_MODEL_CALLS = 1 + MAX_FOLLOW_UPS + MAX_REPAIRS

SPLIT = re.compile(r"[，,；;？?。、\n]|以及|并且|同时|分别|还是|或者|和|与|及")
# "[1]" or "[E2]" in an answer points at evidence; it is not a quoted fact.
MARKER = re.compile(r"[\[【［(（]\s*E?\d+\s*[\]】］)）]")
UNIT = r"个?(?:工作日|自然日|日|天|周|个月|月|年|小时|元|万元|%|％|岁|次|份|张|人)"
QUANTITY = re.compile("(" + NUMBER.pattern + r")\s*(" + UNIT + ")?")
FORBID = ("不得", "禁止", "不能", "不可", "不予", "严禁")
PERMIT = ("可以", "允许", "准予")

CROSSDOC_RULES = (
    "\n6. 问题可能要综合多份资料：每个子问题分别找依据，每条 assertion 只对应一条证据；"
    "claim 里的数字、单位、对象和条件必须都在同一条 assertion 的 quote 中，不得把一份资料的数字套到另一事项。"
    "7. 原文是“不得、禁止”等否定的，不得写成“可以”。"
    "8. 确实缺依据时 outcome=stop，并在 unknowns 中逐条写出还缺哪一项具体内容（事项、对象或条件），便于补查。")


def plan_queries(question, context=""):
    """The whole question first, then its facets; short fragments are dropped."""
    whole = question + context
    queries = [whole]
    for part in SPLIT.split(whole):
        part = part.strip()
        if len(part) >= 4 and part not in queries:
            queries.append(part)
    return queries[:1 + MAX_FACETS]


def extend_pool(pool, units, queries, *, per_query=PER_QUERY, per_source=PER_SOURCE, size=ROUND_SIZE):
    """Append new units to the pool: round-robin over queries, capped per source.

    Existing entries keep their rank, so E-numbers stay stable across rounds.
    Returns the number of units added.
    """
    have = {(item["source_id"], item["locator"]) for item in pool}
    rankings = [retrieve(units, query, per_query) for query in queries]
    counts, added = {}, 0
    for depth in range(per_query):
        for query_index, ranking in enumerate(rankings):
            if depth >= len(ranking) or added >= size:
                continue
            unit = ranking[depth]
            key = (unit["source_id"], unit["locator"])
            if key in have or counts.get(unit["source_id"], 0) >= per_source:
                continue
            have.add(key)
            counts[unit["source_id"]] = counts.get(unit["source_id"], 0) + 1
            pool.append(dict(unit, rank=len(pool) + 1, query=queries[query_index]))
            added += 1
    return added


def _facts(text, locators=()):
    for locator in locators:
        text = text.replace(locator, "")
    return REFERENCE.sub("", MARKER.sub("", text))


def quantities(text):
    """(number, unit) pairs; the unit is "" when none follows the number."""
    return {(number, (unit or "").lstrip("个")) for number, unit in QUANTITY.findall(text)}


def _supported(pairs, quoted):
    """A bare number needs the number in a quote; a number with a unit needs that same pair."""
    numbers = {number for number, _ in quoted}
    return [number + unit for number, unit in sorted(pairs)
            if (number not in numbers if not unit else (number, unit) not in quoted)]


def verify(output, pool):
    """Deterministic checks against the whole evidence pool.

    A stop is not judged here; the flow decides whether it may be final.
    """
    units = {(item["source_id"], item["locator"]): item for item in pool}
    failures = []
    if output["outcome"] == "stop":
        if not output["stop_reason"].strip():
            failures.append("STOP_WITHOUT_REASON")
        return {"passed": not failures, "failures": failures, "sources_cited": 0}
    if not output["assertions"]:
        failures.append("ANSWER_WITHOUT_ASSERTIONS")
    quoted, locators, sources, quote_text = set(), [], set(), ""
    for index, item in enumerate(output["assertions"], 1):
        unit = units.get((item["source_id"], item["locator"]))
        if unit is None:
            failures.append(f"A{index}_CITATION_NOT_RETRIEVED")
            continue
        if not compact(item["quote"]) or compact(item["quote"]) not in compact(unit["text"]):
            failures.append(f"A{index}_QUOTE_NOT_IN_SOURCE")
            continue
        locators.append(item["locator"])
        sources.add(item["source_id"])
        quote_text += compact(item["quote"])
        own = quantities(compact(item["quote"]))
        quoted |= own
        claim = compact(_facts(item["claim"], [item["locator"]]))
        for missing in _supported(quantities(claim), own):
            failures.append(f"A{index}_CLAIM_QUANTITY_NOT_IN_OWN_QUOTE:{missing}")
        for day_unit in DAY_UNITS:
            if day_unit in claim and day_unit not in item["quote"]:
                failures.append(f"A{index}_DAY_UNIT_NOT_IN_OWN_QUOTE:{day_unit}")
        # Heuristic, one direction only: the quote forbids, the claim permits without any negation.
        if (any(word in item["quote"] for word in FORBID) and not any(word in item["quote"] for word in PERMIT)
                and any(word in claim for word in PERMIT) and not any(word in claim for word in FORBID)):
            failures.append(f"A{index}_PROHIBITION_STATED_AS_PERMISSION")
    answer = compact(_facts(output["answer"], locators))
    for missing in _supported(quantities(answer), quoted):
        failures.append("ANSWER_QUANTITY_NOT_QUOTED:" + missing)
    for day_unit in DAY_UNITS:
        if day_unit in answer and day_unit not in quote_text:
            failures.append("DAY_UNIT_NOT_QUOTED:" + day_unit)
    return {"passed": not failures, "failures": failures, "sources_cited": len(sources)}


def _messages(question, pool, context, queries):
    messages = build_messages(question, pool, "proposed", context)
    messages[0]["content"] += CROSSDOC_RULES
    if len(queries) > 1:
        messages[1]["content"] += "\n\n检索时拆出的子问题（仅供逐项核对是否都有依据）：" + "；".join(queries[1:])
    return messages


def _repair(output, failures):
    return [{"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
            {"role": "user", "content": (
                "上一次输出未通过确定性核验，错误码：" + "、".join(failures) + "。\n"
                "请只依据同一组证据重新输出完整 JSON：quote 必须是所标证据原文的连续片段；"
                "claim 和 answer 中的数字与单位必须出现在对应 quote 中；不要写证据编号；"
                "原文为否定的不得写成可以。若证据确实不支持，outcome=stop 并在 unknowns 写明缺项。")}]


def ask(question, units, chat, *, context=""):
    """Answer one question. ``chat(messages)`` returns a deepseek.chat-style result."""
    if type(question) is not str or not question.strip():
        raise ValueError("INVALID_QUESTION")
    started = time.monotonic()
    queries = plan_queries(question, context)
    pool, rounds, calls = [], [], []
    record = {"schema_version": 2, "workflow_version": WORKFLOW, "method": WORKFLOW, "question": question,
              "question_sha256": sha(question), "gate": None, "model": {"called": False, "calls": calls},
              "model_output": None, "checks": None, "stop_origin": None, "follow_ups_used": 0,
              "repairs_used": 0, "status": dict(STATUS)}

    def retrieval(kind, round_queries):
        added = extend_pool(pool, units, round_queries)
        rounds.append({"kind": kind, "queries": round_queries, "added": added})
        return added

    def finish(final, origin=None):
        record.update(final=final, stop_origin=origin, duration_ms=round((time.monotonic() - started) * 1000, 1),
                      retrieval={"version": RETRIEVAL_VERSION, "rounds": rounds, "evidence": [
                          {key: item[key] for key in ("rank", "source_id", "locator", "score", "text_sha256", "query")}
                          for item in pool]})
        return record

    retrieval("initial", queries)
    gate = scope_gate(question + context)
    if gate:
        record["gate"] = gate
        return finish(_stop(gate, GATE_MESSAGES[gate]), "SCOPE_GATE")
    extra = []
    while True:
        messages = _messages(question, pool, context, queries) + extra
        record["input_sha256"] = sha(json.dumps(messages, ensure_ascii=False, sort_keys=True))
        try:
            response = chat(messages)
        except Exception as error:  # Model or contract failures are not "missing evidence".
            record["model"].update(called=True, error=str(error)[:200])
            return finish(_stop("MODEL_ERROR", "模型调用或输出格式失败，本题未作答。"), "MODEL_OR_TOOL_ERROR")
        output = response["output"]
        checks = verify(output, pool)
        calls.append({"model": response.get("model"), "usage": response.get("usage"),
                      "outcome": output["outcome"], "failures": checks["failures"], "pool_size": len(pool)})
        record["model"]["called"] = True
        record.update(model_output=output, checks=checks)
        if output["outcome"] == "stop":
            wanted = [text.strip() for text in [*output["unknowns"], output["stop_reason"]] if len(text.strip()) >= 4]
            if record["follow_ups_used"] < MAX_FOLLOW_UPS and wanted:
                record["follow_ups_used"] += 1
                if retrieval("follow_up", wanted[:MAX_FACETS]):
                    extra = []  # New evidence: ask afresh over the larger pool.
                    continue
                return finish(output, "INSUFFICIENT_EVIDENCE_NOTHING_MORE_RETRIEVED")
            return finish(output, "INSUFFICIENT_EVIDENCE_AFTER_FOLLOW_UP" if record["follow_ups_used"]
                          else "MODEL_STOP_WITHOUT_MISSING_ITEMS")
        if checks["passed"]:
            return finish(output)
        if record["repairs_used"] < MAX_REPAIRS:
            record["repairs_used"] += 1
            extra = _repair(output, checks["failures"])
            continue
        return finish(_stop("VERIFICATION_FAILED", "回答经一次修复后仍未通过引用与数字核验，已改为停止作答。",
                            checks["failures"]), "VERIFICATION_FAILED_AFTER_REPAIR")

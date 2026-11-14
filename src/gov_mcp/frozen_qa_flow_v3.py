"""Bounded answer flow for cross-document questions over the frozen corpus.

frozen_qa.ask("proposed") retrieves once, calls the model once and turns any
failed check or any "not enough evidence" into a stop for the whole question.
This module keeps that retrieval (frozen_retrieval), that verifier
(frozen_qa.verify), the gates, the model contract and the status fields, and
changes only what happens after the first model output:

0. titles     a claim that names a document by its year needs that document's
              title, heading or page date as a citation, and models often omit
              it. When a draft fails, the backend adds those corpus units of
              the cited sources as marked citations, and keeps them only if
              failures disappear. The year rule itself is unchanged: a year
              those units do not contain still fails;
1. follow up  a stop triggers one more retrieval for the missing items it
              names; the model is asked again only if that retrieval adds
              evidence. An answer that passed but lists gaps is returned as
              answer_with_gaps unless follow_up_after_answer is set;
2. repair     an answer that fails verification goes back once with the
              failure codes;
3. salvage    if it still fails, each claim is verified on its own, together
              with the title/date citations of its source. Claims that pass
              are kept as a partial answer. A failed claim whose quote is
              exact source text is replaced by that source sentence, marked
              as an excerpt; other failed claims are dropped and counted in
              unknowns. No unverified wording of the model is displayed.

Every displayed answer passes frozen_qa.verify as a whole. Evidence only
grows within a question and keeps its E-numbers. At most MAX_MODEL_CALLS model
calls per question. Scope, privacy and current-validity gates stop before
retrieval. Results are archive explanations kept PAUSED_UNREVIEWED; this flow
needs its own evaluation and does not change earlier recorded results.
"""
import json
import re
import time

from gov_mcp.frozen_qa import (CURRENT, compact, GATE_MESSAGES, METADATA_KINDS, DOCUMENT_HEADING, STATUS, build_messages,
                               has_cross_document_quantities, normalize_citation_locators, scope_gate, sha, verify,
                               _stop)
from gov_mcp.frozen_retrieval import VERSION as RETRIEVAL_VERSION, identity, retrieve_with_plan, source_key


WORKFLOW = "crossdoc-flow-v3"
MAX_FOLLOW_UPS = 1
MAX_REPAIRS = 1
MAX_MODEL_CALLS = 1 + MAX_FOLLOW_UPS + MAX_REPAIRS
FOLLOW_UP_QUERIES = 3   # missing items searched in one follow-up
FOLLOW_UP_PER_QUERY = 3
FOLLOW_UP_ADDED = 6     # units one follow-up may add

# "[E2]" or a standalone "[1]" points at evidence; it is not a quoted fact.
# A bracketed number inside a document number such as 财综[2007]34号 is left alone.
MARKER = re.compile(r"[\[【［]\s*E\d+\s*[\]】］]"
                    r"|(?<![0-9A-Za-z\u3400-\u9fff])[\[【［]\s*\d{1,2}\s*[\]】］](?!\s*\d)")
# Unknowns about current validity or a person's own case are boundaries, not retrievable gaps.
NOT_RETRIEVABLE = re.compile("|".join(CURRENT) + r"|现行|个人|本人|实际")
TITLE_NOTE = "【系统补充的来源标题】"
EXCERPT_NOTE = "原文摘录："
EXCERPT_WHOLE_UNIT = 200  # a unit this short is excerpted whole
FORBID = ("不得", "禁止", "不能", "不可", "不予", "严禁")
PERMIT = ("可以", "允许", "准予")

FLOW_RULES = (
    "\n6. 复合问题中能由证据支持的部分先作答；确实缺依据的部分不要编写结论，"
    "在 unknowns 中逐条写出缺少的具体内容（事项、对象或条件），系统会据此补查一次。"
    "只有全部子问题都没有依据时才 outcome=stop。"
    "7. 原文是“不得、禁止”等否定的，不得写成“可以”。不要在 answer 或 claim 中写证据编号。")


# "light" asks like the unconstrained baseline, which answered far more cross-document
# questions acceptably in the blind review, and leaves safety to the gates and to the
# claim-by-claim checks afterwards. It carries no topic-specific instructions.
LIGHT_RULES = (
    "\n另外：1. 证据是历史归档，不代表现行政策；不判断今天是否有效、实际应缴金额或个人资格。"
    "2. 每条 assertion 只对应一条证据；claim 中的金额、期限、日期及单位必须出现在它自己的 quote 中，"
    "不同事项、不同分支的数字分开写。"
    "3. 复合问题中能由证据支持的部分先作答；缺依据的部分不要编写结论，在 unknowns 中逐条写明缺什么。"
    "只有全部子问题都没有依据时才 outcome=stop。"
    "4. 原文是“不得、禁止”等否定的，不得写成“可以”。不要在 answer 或 claim 中写证据编号。")
RULE_PROFILES = ("strict", "light")


def clean(output):
    """Display copy without evidence markers; the raw model output stays in the record."""
    return {**output, "answer": MARKER.sub("", output["answer"]),
            "assertions": [{**item, "claim": MARKER.sub("", item["claim"])} for item in output["assertions"]]}


def names_source(unit):
    """Units that the verifier accepts as naming a document: title, heading, page publication date."""
    return (unit.get("evidence_kind") in ("source_title", "page_publication_date")
            or bool(DOCUMENT_HEADING.fullmatch(unit["text"])))


def attach_source_titles(output, pool, units):
    """Cite the corpus units that name each cited source; the pool gains them if absent.

    A title is repeated in the marked claim. A heading or page date is cited with a
    label only, so its own digits are never presented as a policy fact.
    """
    if output["outcome"] != "answer":
        return output
    by_identity = {identity(item): item for item in pool}
    cited = [by_identity[key] for item in output["assertions"]
             if (key := (item["source_id"], item["locator"])) in by_identity]
    sources = {source_key(unit) for unit in cited}
    have = {(item["source_id"], item["locator"]) for item in output["assertions"]}
    added = []
    for unit in units:
        if names_source(unit) and source_key(unit) in sources and identity(unit) not in have:
            if identity(unit) not in by_identity:
                pool.append({**unit, "rank": len(pool) + 1, "score": 0})
                by_identity[identity(unit)] = pool[-1]
            have.add(identity(unit))
            label = unit["text"] if unit.get("evidence_kind") == "source_title" else "（文头或网页发布日期，见引文）"
            added.append({"claim": TITLE_NOTE + label, "source_id": unit["source_id"],
                          "locator": unit["locator"], "quote": unit["text"]})
    return {**output, "assertions": output["assertions"] + added}


def render(output):
    """Numeric cross-document answers are displayed from their checked claims."""
    if not has_cross_document_quantities(output):
        return output
    claims = dict.fromkeys(item["claim"].strip() for item in output["assertions"]
                           if item["claim"].strip() and not item["claim"].startswith(TITLE_NOTE))
    return {**output, "answer": "\n".join(f"- {claim}" for claim in claims)}


def check(output, pool):
    """frozen_qa.verify plus one heuristic: a quoted prohibition stated as permission."""
    result = verify(output, pool)
    failures = list(result["failures"])
    if output["outcome"] == "answer":
        for index, item in enumerate(output["assertions"], 1):
            quote, claim = item["quote"], item["claim"]
            if (any(word in quote for word in FORBID) and not any(word in quote for word in PERMIT)
                    and any(word in claim for word in PERMIT) and not any(word in claim for word in FORBID)):
                failures.append(f"A{index}_PROHIBITION_STATED_AS_PERMISSION")
    return {"passed": not failures, "failures": failures}


def missing_items(output):
    """Retrievable gaps named by the model; a stop also offers its reason."""
    texts = list(output["unknowns"]) + ([output["stop_reason"]] if output["outcome"] == "stop" else [])
    wanted = [text.strip() for text in texts if len(text.strip()) >= 4 and not NOT_RETRIEVABLE.search(text)]
    return list(dict.fromkeys(wanted))[:FOLLOW_UP_QUERIES]


def extend_pool(pool, units, queries):
    """Append units found for the missing items; earlier ranks never change."""
    have = {identity(item) for item in pool}
    added = 0
    for query in queries:
        for unit in retrieve_with_plan(units, query, FOLLOW_UP_PER_QUERY)["evidence"]:
            if identity(unit) not in have and added < FOLLOW_UP_ADDED:
                have.add(identity(unit))
                pool.append({**unit, "rank": len(pool) + 1})
                added += 1
    return added


def sentence_around(text, quote):
    """The whole sentence(s) of a unit that contain the quote, so subject and conditions stay."""
    start = text.find(quote)
    if len(text) <= EXCERPT_WHOLE_UNIT or start < 0:
        return text.strip() if len(text) <= EXCERPT_WHOLE_UNIT else quote.strip()
    ends = [m.end() for m in re.finditer(r"[。；;！？\n]", text)]
    left = max((end for end in ends if end <= start), default=0)
    right = min((end for end in ends if end >= start + len(quote)), default=len(text))
    return text[left:right].strip()


def salvage(output, pool):
    """Keep what verifies claim by claim; return (partial answer or None, dropped, excerpted).

    A claim that fails although its quote is exact source text is replaced by that
    source sentence, marked as an excerpt: the wording is then the source's own.
    Excerpts only accompany at least one verified claim. A draft with no verified
    claim still stops, so excerpts cannot turn a question that should stop into an answer.
    """
    units = {identity(item): item for item in pool}

    def is_support(item):  # A cited title, page date or document heading that names its source.
        unit = units.get((item["source_id"], item["locator"]))
        return unit is not None and (unit.get("evidence_kind") in METADATA_KINDS
                                     or bool(DOCUMENT_HEADING.fullmatch(unit["text"])))

    def passes(items):
        answer = "\n".join(item["claim"] for item in items)
        return check({**output, "answer": answer, "assertions": items}, pool)["passed"]

    assertions = normalize_citation_locators(output, pool)["assertions"]
    support = [item for item in assertions if is_support(item) and passes([item])]

    def with_support(item):
        return [item] + [s for s in support if s["source_id"] == item["source_id"]]

    body, excerpts, dropped = [], [], 0
    for item in assertions:
        if item in support:
            continue
        if passes(with_support(item)):
            body.append(item)
            continue
        unit = units.get((item["source_id"], item["locator"]))
        excerpt = None
        if unit is not None and not is_support(item) and compact(item["quote"]) \
                and compact(item["quote"]) in compact(unit["text"]):
            text = sentence_around(unit["text"], item["quote"])
            excerpt = {**item, "claim": EXCERPT_NOTE + text, "quote": text}
        if excerpt and excerpt not in excerpts and passes(with_support(excerpt)):
            excerpts.append(excerpt)
        elif not excerpt or excerpt not in excerpts:
            dropped += 1
    kept = body + excerpts + support
    if not body or not passes(kept):
        return None, dropped, 0
    answer = "\n".join(f"- {claim}" for claim in dict.fromkeys(item["claim"].strip() for item in body + excerpts))
    note = "（部分答复：" + ("标“原文摘录”的条目，其转述未通过核验，已改为直接摘录原文；" if excerpts else "") \
        + ("另有结论未通过引用或数字核验，未予采用。" if dropped else "摘录内容请对照原文理解。") + "）"
    unknowns = list(output["unknowns"])
    if dropped:
        unknowns.append(f"有{dropped}条结论未通过核验，未予采用；相关子问题请人工查阅原文。")
    return {"outcome": "answer", "answer": answer + "\n" + note, "assertions": kept, "stop_reason": "",
            "unknowns": unknowns}, dropped, len(excerpts)


def settle_draft(raw, pool, units):
    """What one stored model draft becomes with no further model call.

    The same steps ``ask`` applies after its last call: clean, backend source
    citations when they remove failures, checks, then salvage. Used to replay
    recorded drafts offline; a stop draft stays a stop.
    """
    pool = list(pool)
    output = normalize_citation_locators(clean(raw), pool)
    result = {"result_kind": "stop", "claims_dropped": 0, "claims_excerpted": 0, "final": output,
              "source_citations_used": False}
    if output["outcome"] != "answer":
        return result
    checks = check(output, pool)
    if not checks["passed"]:
        supported = attach_source_titles(output, pool, units)
        supported_checks = check(supported, pool)
        if set(supported_checks["failures"]) < set(checks["failures"]):
            output, checks, result["source_citations_used"] = supported, supported_checks, True
    if checks["passed"]:
        final, kind = render(output), "answer_with_gaps" if missing_items(output) else "answer"
    else:
        final, result["claims_dropped"], result["claims_excerpted"] = salvage(output, pool)
        kind = "partial_answer" if final else "stop"
    final = final or _stop("VERIFICATION_FAILED", "没有可通过引用与数字核验的结论。", checks["failures"])
    return {**result, "result_kind": kind, "final": final,
            "final_checks_passed": check(final, pool)["passed"] if kind != "stop" else None}


def _messages(question, pool, context, plan, rules="strict"):
    if rules == "light":
        messages = build_messages(question, pool, "baseline", context)
        messages[0]["content"] += LIGHT_RULES
        return messages
    messages = build_messages(question, pool, "proposed", context, plan)
    messages[0]["content"] += FLOW_RULES
    return messages


def _repair(output, failures):
    return [{"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
            {"role": "user", "content": (
                "上一次输出未通过确定性核验，错误码：" + "、".join(failures) + "。\n"
                "请只依据同一组证据重新输出完整 JSON：source_id 与 locator 照抄证据标注；quote 必须是该证据原文的连续片段；"
                "每条 claim 的金额、期限、日期及单位必须出现在它自己的 quote 中；用年份指称文件时另引该文件的标题或文头；"
                "原文为否定的不得写成可以。无法改正的结论请删除，并把对应缺项写入 unknowns。")}]


def ask(question, units, chat, *, context="", follow_up_after_answer=False, rules="strict"):
    """Answer one question. ``chat(messages)`` returns a deepseek.chat-style result.

    A stop always gets one follow-up retrieval. An answer that passed the checks but
    lists gaps gets one only with ``follow_up_after_answer``: in the two recorded
    eight-question runs that extra call never changed the kind of result.

    ``rules`` picks the generation prompt: "strict" (the proposed chain's rules plus
    the flow rules) or "light" (LIGHT_RULES). Retrieval, gates, checks, repair and
    salvage are the same for both.
    """
    if rules not in RULE_PROFILES:
        raise ValueError("UNKNOWN_RULE_PROFILE")
    if type(question) is not str or not question.strip():
        raise ValueError("INVALID_QUESTION")
    started = time.monotonic()
    query = question + ("\n" + context if context else "")
    pool, rounds, calls = [], [], []
    record = {"schema_version": 3, "workflow_version": WORKFLOW, "method": WORKFLOW, "question": question,
              "question_sha256": sha(question), "rules": rules, "gate": None, "model": {"called": False, "calls": calls},
              "model_output": None, "checks": None, "result_kind": None, "stop_origin": None,
              "follow_ups_used": 0, "repairs_used": 0, "claims_dropped": 0, "claims_excerpted": 0, "status": dict(STATUS)}

    def finish(final, kind, origin=None):
        # final_checks re-verifies what is displayed. An answer whose own unknowns still name a
        # retrievable gap passed the citation checks but does not cover the whole question.
        if kind == "answer" and missing_items(final):
            kind = "answer_with_gaps"
        record.update(final=final, result_kind=kind, stop_origin=origin,
                      final_checks=check(final, pool) if final["outcome"] == "answer" else None,
                      duration_ms=round((time.monotonic() - started) * 1000, 1),
                      retrieval={"version": RETRIEVAL_VERSION, "rounds": rounds, "evidence": [
                          {key: item.get(key) for key in ("rank", "source_id", "locator", "score", "text_sha256")}
                          for item in pool]})
        return record

    gate = scope_gate(query)
    if gate:  # Before retrieval and before any model call.
        record["gate"] = gate
        return finish(_stop(gate, GATE_MESSAGES[gate]), "stop", "SCOPE_GATE")
    packet = retrieve_with_plan(units, query)
    pool.extend(packet["evidence"])
    rounds.append({"kind": "initial", "added": len(pool)})
    extra, verified = [], None  # verified: an answer that already passed, kept while a follow-up is tried
    while True:
        messages = _messages(question, pool, context, packet["plan"], rules) + extra
        record["input_sha256"] = sha(json.dumps(messages, ensure_ascii=False, sort_keys=True))
        try:
            response = chat(messages)
        except Exception as error:  # A model or contract failure is not "missing evidence".
            record["model"].update(called=True, error=str(error)[:200])
            if verified:
                return finish(verified, "answer")
            return finish(_stop("MODEL_ERROR", "模型调用或输出格式失败，本题未作答。"), "stop", "MODEL_OR_TOOL_ERROR")
        raw = response["output"]
        output = normalize_citation_locators(clean(raw), pool)
        checks = check(output, pool)
        if output["outcome"] == "answer" and not checks["passed"]:
            # Backend source citations are used only when they remove failures and add none.
            supported = attach_source_titles(output, pool, units)
            supported_checks = check(supported, pool)
            if set(supported_checks["failures"]) < set(checks["failures"]):
                output, checks = supported, supported_checks
        calls.append({"model": response.get("model"), "usage": response.get("usage"), "outcome": raw["outcome"],
                      "failures": checks["failures"], "pool_size": len(pool),
                      # Counts per draft show whether a follow-up or repair added or lost claims.
                      "assertions": len(raw["assertions"]), "unknowns": len(raw["unknowns"])})
        record["model"]["called"] = True
        record.update(model_output=raw, checks=checks)  # checks: the latest draft, not the final
        good = output["outcome"] == "answer" and checks["passed"]
        if good or output["outcome"] == "stop":
            wanted = missing_items(output)
            if record["follow_ups_used"] < MAX_FOLLOW_UPS and wanted and (follow_up_after_answer or not good):
                record["follow_ups_used"] += 1
                added = extend_pool(pool, units, wanted)
                rounds.append({"kind": "follow_up", "queries": wanted, "added": added})
                if added:
                    verified, extra = (render(output) if good else None), []
                    continue
            if good and not (verified and len(output["assertions"]) < len(verified["assertions"])):
                return finish(render(output), "answer")
            if verified:  # The follow-up did not improve on the answer that had passed.
                return finish(verified, "answer")
            return finish(output, "stop", "INSUFFICIENT_EVIDENCE_AFTER_FOLLOW_UP" if record["follow_ups_used"]
                          else "MODEL_STOP_WITHOUT_MISSING_ITEMS")
        if verified:  # The answer that already passed beats repairing or trimming a worse draft.
            return finish(verified, "answer")
        if record["repairs_used"] < MAX_REPAIRS and len(calls) < MAX_MODEL_CALLS:
            record["repairs_used"] += 1
            extra = _repair(raw, checks["failures"])
            continue
        partial, record["claims_dropped"], record["claims_excerpted"] = salvage(output, pool)
        if partial:
            return finish(partial, "partial_answer")
        return finish(_stop("VERIFICATION_FAILED", "回答经修复后仍没有可通过引用与数字核验的结论，已停止作答。",
                            checks["failures"]), "stop", "VERIFICATION_FAILED_AFTER_REPAIR")

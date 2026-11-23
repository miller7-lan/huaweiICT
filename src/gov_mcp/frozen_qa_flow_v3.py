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

The displayed answer is constructed from checked claims and verified as a whole. Evidence only
grows within a question and keeps its E-numbers. At most MAX_MODEL_CALLS model
calls per question. Scope, privacy and current-validity gates stop before
retrieval. Results are archive explanations kept PAUSED_UNREVIEWED; this flow
needs its own evaluation and does not change earlier recorded results.
"""
import json
import re
import time

from gov_mcp.frozen_qa import (CALENDAR_DATE, CELL_ADDRESS, CURRENT, canonical_number, compact, GATE_MESSAGES,
                               METADATA_KINDS, DOCUMENT_HEADING,
                               STATUS, TYPED_DATE, build_messages, date_value, normalize_citation_locators,
                               quantity_pairs, scope_gate, sha, typed_date, verify,
                               _stop)
from gov_mcp.frozen_retrieval import (FIRST_ISSUE_EFFECTIVE_DATE, FIRST_ISSUE_WAIVER, TABLE_ROW,
                                      VERSION as RETRIEVAL_VERSION, EvidenceIndex, identity, retrieve_with_plan, source_key)


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
GAP_LABELS = frozenset(("邮费", "邮寄费用", "邮费金额", "工本费", "收费标准", "有效期", "办理时限",
                       "材料", "所需材料", "适用对象", "适用范围", "办理流程", "现行效力"))
UNVERIFIED_GAP = "另有事项缺少可核验依据。"
MODEL_STOP_MESSAGE = "冻结资料缺少足够依据，本题停止作答。"
WAIVER_SCOPE_QUESTION = re.compile(r"(?:停征|免征|免收)[^。！？]{0,36}(?:包括|涵盖|覆盖|列入|适用)")
WAIVER_SCOPE_BRANCHES = ("到期换领", "遗失补领")

FLOW_RULES = (
    "\n6. 按题目的每个分问分别给出有引文支持的断言。问某条款是否覆盖其他情形时，"
    "先引用该条款并明确其他情形是否被该条款列入，范围只限该归档文件。"
    "在 unknowns 中只列题目确实问到但证据缺少的事项、对象或条件，系统可据此补查一次。"
    "所有分问已回答时，unknowns 必须是空数组；未问到的现行效力、具体金额、例外或后续变动不计为缺项。"
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


def is_metadata(unit):
    return unit.get("evidence_kind") in METADATA_KINDS or names_source(unit)


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
    """Build the answer from its claims, excluding backend citation labels."""
    claims = dict.fromkeys(item["claim"].strip() for item in output["assertions"]
                          if item["claim"].strip() and not item["claim"].startswith(TITLE_NOTE))
    return {**output, "answer": "\n".join(f"- {claim}" for claim in claims)}


# A document number such as 豫公〔2021〕16号 or 财综[2007]34号 names a document; its digits are
# neither a quantity nor a year claim. Brackets vary between sources, so they are compared unified.
DOCUMENT_NUMBER = re.compile(r"[\u4e00-\u9fff]{1,8}\s*[〔\[［(（]\s*\d{4}\s*[〕\]］)）]\s*\d+\s*号")
DOCUMENT_ITEM = rf"《?\s*{DOCUMENT_NUMBER.pattern}\s*》?"
LISTED_DOCUMENT_COUNT = re.compile(
    rf"(?P<documents>{DOCUMENT_ITEM}(?:\s*[、，,和与及]\s*{DOCUMENT_ITEM})+)"
    r"\s*(?P<count>\d+|[一二两三四五六七八九十]+)\s*份(?=文件|通知|文书)"
)
ISSUER_PREFIX = re.compile(
    r"(?:列出|列明|列有|列示|引用|列为)(?:了|的)?(?:是|为)?|(?:收费标准(?:列)?|文号)(?:是|为)"
    r"|分别(?:是|为)|包括|依据|根据|按照|参照|执行|引自|所引|见|按|和|与|及"
)
NUMBER_MASK = "〔所引文号〕"


def _document_number_key(text):
    """Normalize bracket spelling and recognized prefixes, preserving the complete issuer."""
    match = re.fullmatch(r"(?P<issuer>[\u4e00-\u9fff]+)[〔\[［(（](?P<year>\d{4})[〕\]］)）](?P<number>\d+)号", compact(text))
    if match is None:
        return compact(text)
    issuer = ISSUER_PREFIX.split(match["issuer"])[-1] or match["issuer"]
    return f"{issuer}〔{match['year']}〕{match['number']}号"


def mask_listed_document_count(text, allowed):
    """The count of an explicitly listed set of quoted document numbers is derived from that list."""
    def replace(match):
        numbers = {_document_number_key(entry.group()) for entry in DOCUMENT_NUMBER.finditer(match["documents"])}
        if numbers <= allowed and str(len(numbers)) == canonical_number(match["count"]):
            return match["documents"] + " "
        return match.group()

    return LISTED_DOCUMENT_COUNT.sub(replace, text)


def mask_quoted_document_numbers(output, pool):
    """Hide, for the number checks only, document numbers that a valid quote of the same source contains.

    A claim may name a document number only if one of the cited quotes of its own source
    holds the same number (the answer may use any cited source). Everything else, including
    a document number nobody quoted, is left for the checks as it is. The displayed output
    is never changed by this.
    """
    units = {(unit["source_id"], unit["locator"]): unit for unit in pool}
    quoted = {}
    for item in output["assertions"]:
        unit = units.get((item["source_id"], item["locator"]))
        if unit is None or not compact(item["quote"]) or compact(item["quote"]) not in compact(unit["text"]):
            continue
        for match in DOCUMENT_NUMBER.finditer(item["quote"]):
            quoted.setdefault(source_key(unit), set()).add(_document_number_key(match.group()))

    def mask(text, sources):
        allowed = set().union(*(quoted.get(source, set()) for source in sources))
        return DOCUMENT_NUMBER.sub(lambda m: NUMBER_MASK if _document_number_key(m.group()) in allowed else m.group(),
                                   mask_listed_document_count(text, allowed))

    return {**output, "answer": mask(output["answer"], set(quoted)),
            "assertions": [{**item, "claim": mask(item["claim"], {source_key(unit)} if
                            (unit := units.get((item["source_id"], item["locator"]))) else set())}
                           for item in output["assertions"]]}


def metadata_claim(claim, quote, unit):
    """Allow metadata to identify a source without using a heading as business evidence."""
    content = compact(claim).removeprefix(TITLE_NOTE).rstrip("。")
    original = compact(quote).rstrip("。")
    if content in {"来源标题", "网页标题", "文件标题", "文头", "（文头或网页发布日期，见引文）"}:
        return "support"
    if content == original or any(content == prefix + original for prefix in
                                  ("网页标题为", "网页标题是", "文件标题为", "文件标题是", "标题为", "标题是")):
        return "claim"
    if unit.get("evidence_kind") == "source_title" and "目录元数据" in unit["locator"]:
        catalogue = re.fullmatch(
            r"(?:(?:该)?(?P<subject>[^，。；：:]{1,60}?)(?:的年份)?(?:出处|来源)(?:为|是))?"
            r"资料目录登记(?:标题(?:为|是|[：:])|的)?(?P<title>.+)", content
        )
        if (catalogue and catalogue["title"].replace("《", "").replace("》", "") == original
                and (not catalogue["subject"] or catalogue["subject"] in original)):
            return "claim"
    if unit.get("evidence_kind") in {"page_publication_date", "page_modified_date"} and TYPED_DATE.fullmatch(content):
        return "claim"
    if DOCUMENT_HEADING.fullmatch(unit["text"]) and (printed := CALENDAR_DATE.match(original)):
        date = date_value(printed.group())
        typed = TYPED_DATE.fullmatch(content)
        if typed and typed_date(typed) == ("document_date", date):
            return "claim"
        identification = re.fullmatch(
            rf"(?P<year>\d{{4}})年(?:的)?(?:通知|文件)(?:的年份)?(?:由)?文头(?:日期)?"
            rf"(?:标明|载明|注明)?(?:为|是)(?P<date>{CALENDAR_DATE.pattern})", content
        )
        if (identification and date and identification["year"] == date[:4]
                and date_value(identification["date"]) == date):
            return "claim"
    return None


def table_claim_failure(claim, columns):
    """A named table column supplies only its own cell address, quantities and document numbers."""
    values = {name: (value, address) for name, value, address in columns if name}
    if not values:
        return None
    names = sorted(values, key=len, reverse=True)
    parts = re.split("(" + "|".join(map(re.escape, names)) + ")", claim)
    for name, text in zip(parts[1::2], parts[2::2]):
        value, address = values[name]
        following = re.match(
            rf"\s*(?:列|栏)?\s*(?:[（(：:]|(?:对应|位于|为|是)\s*(?:单元格)?|(?:单元格|地址)(?:为|是)?)"
            rf"\s*(?P<cell>{CELL_ADDRESS.pattern})", text
        )
        preceding = re.finditer(
            rf"(?P<cell>{CELL_ADDRESS.pattern})\s*(?:单元格)?\s*(?:[（(]|为|是|对应)\s*{re.escape(name)}", claim
        )
        linked = [match["cell"] for match in preceding]
        if following:
            linked.append(following["cell"])
        if any(cell.replace("$", "") != address.replace("$", "") for cell in linked):
            return "TABLE_COLUMN_CELL_MISMATCH"
        stated = {_document_number_key(match.group()) for match in DOCUMENT_NUMBER.finditer(text)}
        cited = {_document_number_key(match.group()) for match in DOCUMENT_NUMBER.finditer(value)}
        if not stated <= cited:
            return "DOCUMENT_NUMBER_COLUMN_MISMATCH"
        if not quantity_pairs(mask_listed_document_count(text, cited)) <= quantity_pairs(value):
            return "QUANTITY_COLUMN_MISMATCH"
    return None


def check(output, pool):
    """frozen_qa.verify on quoted document numbers masked, plus one heuristic: a quoted
    prohibition stated as permission."""
    output = normalize_citation_locators(output, pool)
    masked = mask_quoted_document_numbers(output, pool)
    result = verify(masked, pool)
    failures = list(result["failures"])
    if output["outcome"] == "answer":
        units = {identity(unit): unit for unit in pool}
        columns = table_columns(pool)
        displayable = False
        if DOCUMENT_NUMBER.search(masked["answer"]):
            failures.append("ANSWER_DOCUMENT_NUMBER_NOT_QUOTED")
        for index, item in enumerate(output["assertions"], 1):
            quote, claim = item["quote"], item["claim"]
            if DOCUMENT_NUMBER.search(masked["assertions"][index - 1]["claim"]):
                failures.append(f"A{index}_DOCUMENT_NUMBER_NOT_QUOTED")
            key = (item["source_id"], item["locator"])
            unit = units.get(key)
            if not claim.strip():
                failures.append(f"A{index}_EMPTY_CLAIM")
            elif unit is not None and is_metadata(unit):
                purpose = metadata_claim(claim, quote, unit)
                if purpose is None:
                    failures.append(f"A{index}_METADATA_CANNOT_SUPPORT_CLAIM")
                displayable |= purpose == "claim" and not claim.startswith(TITLE_NOTE)
            elif claim.startswith(TITLE_NOTE):
                failures.append(f"A{index}_INVALID_SOURCE_TITLE_LABEL")
            else:
                displayable = True
                if failure := table_claim_failure(claim, columns.get(key, ())):
                    failures.append(f"A{index}_{failure}")
            if (any(word in quote for word in FORBID) and not any(word in quote for word in PERMIT)
                    and any(word in claim for word in PERMIT) and not any(word in claim for word in FORBID)):
                failures.append(f"A{index}_PROHIBITION_STATED_AS_PERMISSION")
        if not output["answer"].strip() or not displayable:
            failures.append("ANSWER_WITHOUT_DISPLAYABLE_CLAIMS")
    return {"passed": not failures, "failures": list(dict.fromkeys(failures))}


def safe_unknowns(items):
    """Display controlled gap labels, never model-generated assertions in the gap list."""
    labels = [item.strip() if item.strip() in GAP_LABELS else UNVERIFIED_GAP for item in items if item.strip()]
    return list(dict.fromkeys(labels))


def display_model_stop(output):
    return _stop("MODEL_EVIDENCE_INSUFFICIENT", MODEL_STOP_MESSAGE, safe_unknowns(output["unknowns"]))


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
    """Return the source sentence or sentences containing a verified quote."""
    if len(text) <= EXCERPT_WHOLE_UNIT:
        return text.strip()
    start = text.find(quote)
    if start >= 0:
        end = start + len(quote)
    else:
        # Citation matching ignores whitespace. Map the normalized match back to the source.
        normalized_quote = compact(quote)
        match = compact(text).find(normalized_quote)
        if not normalized_quote or match < 0:
            return ""
        positions = [item.start() for item in re.finditer(r"\S", text)]
        start = positions[match]
        end = positions[match + len(normalized_quote) - 1] + 1
    ends = [item.end() for item in re.finditer(r"[。；;！？\n]", text)]
    left = max((boundary for boundary in ends if boundary <= start), default=0)
    right = min((boundary for boundary in ends if boundary >= end), default=len(text))
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
        return unit is not None and is_metadata(unit)

    def passes(items):
        answer = "\n".join(item["claim"] for item in items)
        failures = check({**output, "answer": answer, "assertions": items}, pool)["failures"]
        return not [failure for failure in failures if failure != "ANSWER_WITHOUT_DISPLAYABLE_CLAIMS"]

    assertions = normalize_citation_locators(output, pool)["assertions"]
    support = [item for item in assertions if is_support(item) and passes([item])]

    def with_support(item):
        return [item] + [s for s in support if s["source_id"] == item["source_id"]]

    body, excerpts, dropped = [], [], 0
    for item in assertions:
        if item in support:
            continue
        if not item["claim"].strip():
            dropped += 1
            continue
        if passes(with_support(item)):
            body.append(item)
            continue
        unit = units.get((item["source_id"], item["locator"]))
        excerpt = None
        if unit is not None and not is_support(item) and compact(item["quote"]) \
                and compact(item["quote"]) in compact(unit["text"]):
            text = sentence_around(unit["text"], item["quote"])
            if text:
                excerpt = {**item, "claim": EXCERPT_NOTE + text, "quote": text}
        if excerpt and excerpt not in excerpts and passes(with_support(excerpt)):
            excerpts.append(excerpt)
        elif not excerpt or excerpt not in excerpts:
            dropped += 1
    kept = body + excerpts + support
    if not body:
        return None, dropped, 0
    answer = "\n".join(f"- {claim}" for claim in dict.fromkeys(item["claim"].strip() for item in body + excerpts))
    note = "（部分答复：" + ("标“原文摘录”的条目，其转述未通过核验，已改为直接摘录原文；" if excerpts else "") \
        + ("另有结论未通过引用或数字核验，未予采用。" if dropped else "摘录内容请对照原文理解。") + "）"
    unknowns = list(output["unknowns"])
    if dropped:
        unknowns.append(f"有{dropped}条结论未通过核验，未予采用；相关子问题请人工查阅原文。")
    final = {"outcome": "answer", "answer": answer + "\n" + note, "assertions": kept,
             "stop_reason": "", "unknowns": unknowns}
    if not check(final, pool)["passed"]:
        return None, dropped, 0
    return final, dropped, len(excerpts)


def check_draft(raw, pool, units):
    """Check the actual display, including any support later attached from the corpus."""
    output = normalize_citation_locators(clean(raw), pool)
    if output["outcome"] == "answer":
        output = render(output)
    checks = check(output, pool)
    source_citations_used = False
    if output["outcome"] != "answer":
        return output, checks, source_citations_used
    if not checks["passed"]:
        supported = attach_source_titles(output, pool, units)
        supported_checks = check(supported, pool)
        if set(supported_checks["failures"]) < set(checks["failures"]):
            output, checks, source_citations_used = supported, supported_checks, True
    return output, checks, source_citations_used


def settle_draft(raw, pool, units):
    """Replay a stored draft without follow-up or repair calls."""
    pool = list(pool)
    output, checks, source_citations_used = check_draft(raw, pool, units)
    result = {"result_kind": "stop", "claims_dropped": 0, "claims_excerpted": 0, "final": output,
              "source_citations_used": source_citations_used}
    if output["outcome"] != "answer":
        result["final"] = display_model_stop(output)
        return result
    if checks["passed"]:
        final, kind = output, label_kind(output, "answer")
    else:
        final, result["claims_dropped"], result["claims_excerpted"] = salvage(output, pool)
        kind = "partial_answer" if final else "stop"
    final = final or _stop("VERIFICATION_FAILED", "没有可通过引用与数字核验的结论。")
    return {**result, "result_kind": kind, "final": {**final, "unknowns": safe_unknowns(final["unknowns"])},
            "final_checks_passed": True if kind != "stop" else None}


def new_state():
    """Per-question state shared by ``ask`` and the MCP service; JSON-serializable."""
    return {"pool": [], "rounds": [], "calls": 0, "follow_ups_used": 0, "repairs_used": 0, "verified": None,
            "claims_dropped": 0, "claims_excerpted": 0}


def advance(state, raw, units, *, follow_up_after_answer=False):
    """Decide what one model draft leads to. The state is updated in place.

    Returns ``checks`` of this draft and an ``action``:
    - "final": ``final``, ``kind`` and ``origin`` say what is displayed;
    - "repair": the draft failed the checks and may be rewritten once (``failures``);
    - "more_evidence": a follow-up retrieval added ``added`` units; ask again over the larger pool.
    """
    pool = state["pool"]
    state["calls"] += 1
    output, checks, _ = check_draft(raw, pool, units)
    verified = state["verified"]  # an answer kept while a follow-up is tried
    if verified is not None:
        # Persisted tasks may have stored an answer rendered by an earlier implementation.
        verified = render(verified)
        if not verified["answer"] or not check(verified, pool)["passed"]:
            verified = None
        state["verified"] = verified

    def final(value, kind, origin=None):
        return {"action": "final", "final": value, "kind": kind, "origin": origin, "checks": checks}

    good = output["outcome"] == "answer" and checks["passed"]
    if good or output["outcome"] == "stop":
        wanted = missing_items(output)
        if state["follow_ups_used"] < MAX_FOLLOW_UPS and wanted and (follow_up_after_answer or not good) \
                and state["calls"] < MAX_MODEL_CALLS:
            state["follow_ups_used"] += 1
            before = len(pool)
            added = extend_pool(pool, units, wanted)
            state["rounds"].append({"kind": "follow_up", "queries": wanted, "added": added})
            if added:
                state["verified"] = output if good else None
                return {"action": "more_evidence", "added": pool[before:], "checks": checks}
        if good and not (verified and len(output["assertions"]) < len(verified["assertions"])):
            return final(output, "answer")
        if verified:  # The follow-up did not improve on the answer that had passed.
            return final(verified, "answer")
        return final(display_model_stop(output), "stop", "INSUFFICIENT_EVIDENCE_AFTER_FOLLOW_UP" if state["follow_ups_used"]
                     else "MODEL_STOP_WITHOUT_MISSING_ITEMS")
    if verified:  # The answer that already passed beats repairing or trimming a worse draft.
        return final(verified, "answer")
    if state["repairs_used"] < MAX_REPAIRS and state["calls"] < MAX_MODEL_CALLS:
        state["repairs_used"] += 1
        return {"action": "repair", "failures": checks["failures"], "checks": checks}
    partial, state["claims_dropped"], state["claims_excerpted"] = salvage(output, pool)
    if partial:
        return final(partial, "partial_answer")
    return final(_stop("VERIFICATION_FAILED", "回答经修复后仍没有可通过引用与数字核验的结论，已停止作答。"),
                 "stop", "VERIFICATION_FAILED_AFTER_REPAIR")


def label_kind(final, kind):
    """A declared gap affects completeness even when it is unsuitable for retrieval."""
    if kind == "answer" and any(item.strip() for item in final["unknowns"]):
        return "answer_with_gaps"
    return kind


def native_display_text(final, kind):
    lines = [final["answer"]]
    unknowns = final["unknowns"]
    if unknowns and kind in {"partial_answer", "answer_with_gaps"}:
        lines.append("部分事项仍缺少可核验依据。")
    for claim in final["assertions"]:
        lines.append(f"「{claim['quote']}」—— {claim['source_id']} {claim['locator']}")
    if unknowns:
        lines.append("未知项：" + "、".join(unknowns))
    lines.append("说明：以上为冻结的历史归档资料解释，未核定现行效力，需人工复核。")
    return "\n".join(lines)


def complete_first_issue_scope(final, question):
    """Answer an explicitly asked scope comparison from the cited first-issue waiver."""
    if not WAIVER_SCOPE_QUESTION.search(question):
        return final
    asked = [branch for branch in WAIVER_SCOPE_BRANCHES if branch in question]
    waiver = [item for item in final["assertions"]
              if (item["source_id"], item["locator"]) == FIRST_ISSUE_WAIVER]
    missing = [branch for branch in asked if all(branch not in item["claim"] for item in waiver)]
    if not missing:
        return final
    clause = f"自{FIRST_ISSUE_EFFECTIVE_DATE}起停征首次申领居民身份证工本费"
    proof = next((item for item in waiver if clause in compact(item["quote"]).replace("，", "")), None)
    if proof is None:
        return final
    conclusion = "该停征条款列明的对象是首次申领居民身份证工本费，未将" + "、".join(missing) + "列入。"
    return {**final, "answer": f"{final['answer']}\n- {conclusion}",
            "assertions": [*final["assertions"], {**proof, "claim": conclusion}]}


def checked_final(final, pool, kind, origin, *, question=""):
    """Refuse a final answer if the exact result being returned fails verification."""
    if final["outcome"] == "answer":
        final = complete_first_issue_scope(final, question)
        final = {**final, "unknowns": safe_unknowns(final["unknowns"])}
        checks = check(final, pool)
        if not final["answer"].strip() or not checks["passed"]:
            reason = "FINAL_VERIFICATION_FAILED"
            final = _stop(reason, "最终答复未通过引用与数字核验。")
            return final, "stop", reason, None
        return final, label_kind(final, kind), origin, checks
    return final, kind, origin, None


COLUMN_GUIDE = ("\n\n表格行的列对照（系统按同一工作表的表头生成，只帮助看清每个值属于哪一列；"
                "它不是证据，quote 仍须取自上面证据的原文）：\n")


def table_columns(units):
    """Column name and value of every table row whose header row is among ``units``.

    Returns {(source_id, locator): [(column name, value, cell address), ...]}. A table row
    is stored as bare cell values, so a fee and its legal basis look alike without the
    header. Cells are paired by column letter only, with the nearest header above the row
    in the same sheet of the same source; a row or header whose text does not split into
    exactly its recorded cells is left out rather than guessed.
    """
    def columns(unit):
        values, cells = unit["text"].split(" | "), unit.get("cells") or []
        if len(values) != len(cells):
            return None
        return {re.sub(r"\d+", "", address): (value, address) for address, value in zip(cells, values)}

    headers = [unit for unit in units if EvidenceIndex.is_header(unit) and TABLE_ROW.fullmatch(unit["locator"])]
    result = {}
    for unit in units:
        row = TABLE_ROW.fullmatch(unit["locator"]) if unit.get("evidence_kind") == "xlsx_row" else None
        if row is None or EvidenceIndex.is_header(unit):
            continue
        above = [other for other in headers if source_key(other) == source_key(unit)
                 and (match := TABLE_ROW.fullmatch(other["locator"]))[1] == row[1] and int(match[2]) < int(row[2])]
        if not above:
            continue
        header = max(above, key=lambda other: int(TABLE_ROW.fullmatch(other["locator"])[2]))
        names, values = columns(header), columns(unit)
        if names is None or values is None:
            continue
        pairs = [(names[column][0], value, address) for column, (value, address) in values.items() if column in names]
        if pairs:
            result[identity(unit)] = pairs
    return result


def column_guide(pool):
    """Display aid for the table rows in the pool (see ``table_columns``); nothing here can be cited,
    and the checks still read the original unit text."""
    columns = table_columns(pool)
    lines = [f"[E{unit['rank']}] " + "；".join(f"{name}＝{value}（单元格 {address}）"
                                             for name, value, address in columns[identity(unit)])
             for unit in pool if identity(unit) in columns]
    return COLUMN_GUIDE + "\n".join(lines) if lines else ""


def _messages(question, pool, context, plan, rules="strict"):
    if rules == "light":
        messages = build_messages(question, pool, "baseline", context)
        messages[0]["content"] += LIGHT_RULES
    else:
        messages = build_messages(question, pool, "proposed", context, plan)
        messages[0]["content"] += FLOW_RULES
    messages[1]["content"] += column_guide(pool)
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
    record = {"schema_version": 3, "workflow_version": WORKFLOW, "method": WORKFLOW, "question": question, "context": context,
              "question_sha256": sha(question), "rules": rules, "gate": None, "model": {"called": False, "calls": calls},
              "model_output": None, "checks": None, "result_kind": None, "stop_origin": None,
              "follow_ups_used": 0, "repairs_used": 0, "claims_dropped": 0, "claims_excerpted": 0, "status": dict(STATUS)}

    def finish(final, kind, origin=None):
        final, kind, origin, checks = checked_final(final, pool, kind, origin, question=question)
        record.update(final=final, result_kind=kind, stop_origin=origin, final_checks=checks,
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
    state = new_state()
    state["pool"], state["rounds"] = pool, rounds
    extra = []
    while True:
        messages = _messages(question, pool, context, packet["plan"], rules) + extra
        record["input_sha256"] = sha(json.dumps(messages, ensure_ascii=False, sort_keys=True))
        try:
            response = chat(messages)
        except Exception as error:  # A model or contract failure is not "missing evidence".
            error_type = type(error).__name__
            calls.append({"outcome": "error", "error_type": error_type, "usage": None,
                          "failures": [], "pool_size": len(pool)})
            record["model"].update(called=True, error=error_type)
            if state["verified"]:
                return finish(state["verified"], "answer")
            return finish(_stop("MODEL_ERROR", "模型调用或输出格式失败，本题未作答。"), "stop", "MODEL_OR_TOOL_ERROR")
        raw = response["output"]
        size = len(pool)
        step = advance(state, raw, units, follow_up_after_answer=follow_up_after_answer)
        calls.append({"model": response.get("model"), "usage": response.get("usage"), "outcome": raw["outcome"],
                      "failures": step["checks"]["failures"], "pool_size": size,
                      # Counts per draft show whether a follow-up or repair added or lost claims.
                      "assertions": len(raw["assertions"]), "unknowns": len(raw["unknowns"])})
        record["model"]["called"] = True
        record.update(model_output=raw, checks=step["checks"],  # checks: the latest draft, not the final
                      follow_ups_used=state["follow_ups_used"], repairs_used=state["repairs_used"],
                      claims_dropped=state["claims_dropped"], claims_excerpted=state["claims_excerpted"])
        if step["action"] == "final":
            return finish(step["final"], step["kind"], step["origin"])
        extra = _repair(raw, step["failures"]) if step["action"] == "repair" else []

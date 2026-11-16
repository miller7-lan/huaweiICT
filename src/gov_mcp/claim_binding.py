"""Clause-level checks that bind each claim to what its own quote says.

frozen_qa.verify checks that a claim's numbers and units occur somewhere in its quote. It
cannot see which business branch or attribute a number belongs to, whether the quote
restricts the statement ("有条件的窗口"), or Chinese numerals in units it does not list. The
functions here add those checks for flow V3 without changing the model contract or
frozen_qa.verify (the earlier chains keep their recorded behaviour):

- quantities are read with Chinese numerals normalized ("三个月" == "3个月") and with the
  units 次, 个月, 周, 小时, 岁, 份, 张, 项 that verify does not read;
- a quantity in a claim clause must occur in a clause of its own quote, and the branch
  (首次申领, 到期换领, 损坏换领, 补领, 临时证) and attribute (工本费, 邮费, 时限, 有效期...)
  named around it must not contradict that quote clause;
- a restriction in the quote (有条件, 仅限, 除外...) must not be dropped, and a claim must not
  generalize (所有, 一律, 均可...) or guarantee what the quote does not;
- document numbers compare the full issuer abbreviation;
- the year by which a claim names a document must agree with that document's title or
  heading when they carry a year.

All checks are deterministic and lexical. They catch the recorded failure patterns; they are
not a semantic entailment check, and a statement outside these lexicons is not judged.
"""
import re

from gov_mcp.frozen_qa import CALENDAR_DATE, DATE_ROLE, DOCUMENT_HEADING, DOCUMENT_YEAR, YEAR, compact


# ---------------------------------------------------------------- numerals and quantities
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_SMALL = {"十": 10, "百": 100, "千": 1000}
CN_BIG = {"万": 10 ** 4, "亿": 10 ** 8}
CN_NUMERAL = "零〇一二两三四五六七八九十百千万亿"


def cn_to_int(text):
    """Chinese numeral to int: "二十" 20, "一百零五" 105, "二〇〇七" 2007; None if not a numeral."""
    if not text or any(char not in CN_NUMERAL for char in text):
        return None
    if len(text) > 1 and all(char in CN_DIGIT for char in text):  # read digit by digit
        return int("".join(str(CN_DIGIT[char]) for char in text))
    total, section, digit = 0, 0, None
    for char in text:
        if char in CN_DIGIT:
            digit = CN_DIGIT[char]
        elif char in CN_SMALL:
            section += (1 if digit is None else digit) * CN_SMALL[char]
            digit = None
        else:  # 万 / 亿
            section += digit or 0
            total = (total + section) * CN_BIG[char] if char == "亿" else total + section * CN_BIG[char]
            section, digit = 0, None
    return total + section + (digit or 0)


def number_value(text):
    """Canonical string of an Arabic or Chinese number, or None."""
    text = text.replace(",", "").replace("，", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return str(int(text)) if "." not in text else text
    value = cn_to_int(text)
    return None if value is None else str(value)


NUM = rf"\d+(?:[,，]\d{{3}})*(?:\.\d+)?|[{CN_NUMERAL}]+"
# 个 is required before 月 so that "3月" in a date is never read as three months.
QUANTITY = re.compile(
    rf"(?P<number>{NUM})\s*(?:个\s*)?"
    rf"(?P<unit>万元|元|工作日|自然日|小时|(?<=个)月|(?<=个\s)月|周|天|岁|次(?!性)|份|张|项|年|日)"
)
PARTIAL_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]")
DOCUMENT_NUMBER = re.compile(r"[一-鿿]{1,10}\s*[〔\[［(（]\s*\d{4}\s*[〕\]］)）]\s*\d+\s*号")
POSITION = re.compile(r"第\s*[\d一二三四五六七八九十百]+\s*[行页条项款段章节]|段落\s*\d+|(?<![A-Za-z0-9])[A-Z]{1,3}\d{1,5}(?![A-Za-z0-9])")


def mask_non_quantities(text):
    """Blank out dates, document numbers, positions and cell addresses before reading quantities."""
    for pattern in (DOCUMENT_NUMBER, CALENDAR_DATE, PARTIAL_DATE, POSITION):
        text = pattern.sub(lambda m: " " * len(m.group()), text)
    return text


def quantities(text):
    """{(value, unit)} with numbers normalized; four-digit years are not quantities."""
    found = set()
    for match in QUANTITY.finditer(mask_non_quantities(text)):
        value = number_value(match["number"])
        if value is None:
            continue
        if match["unit"] == "年" and len(value) == 4:
            continue
        found.add((value, match["unit"]))
    return found


def arabic_quantities(text):
    """Display-neutral copy for verify: Chinese numerals before a unit become Arabic digits."""
    def replace(match):
        number = match["number"]
        if not re.fullmatch(rf"[{CN_NUMERAL}]+", number):
            return match.group()
        value = number_value(number)
        return match.group() if value is None else value + match.group()[len(number):]
    return QUANTITY.sub(replace, text)


# ---------------------------------------------------------------- clauses, branches, attributes
# A claim is split at 、 too ("首次申领40元、损坏换领20元" states two facts); a quote is not, because
# an enumeration there shares one predicate ("首次申领、换领每证20元").
CLAUSE_END = re.compile(r"[，,；;。！？!?\n、]")
QUOTE_CLAUSE_END = re.compile(r"[，,；;。！？!?\n]")
SENTENCE_END = re.compile(r"[；;。！？!?\n]")

# Business branches; the longest name wins. "renew" is compatible with both of its sub-branches.
BRANCH_WORDS = (
    ("首次申领", "first"), ("首次办理", "first"), ("首次", "first"), ("初次申领", "first"),
    ("到期换领", "renew.expire"), ("期满换领", "renew.expire"), ("有效期满", "renew.expire"),
    ("损坏换领", "renew.damage"), ("损坏", "renew.damage"),
    ("遗失补领", "reissue"), ("丢失补领", "reissue"), ("补领", "reissue"), ("遗失", "reissue"), ("丢失", "reissue"),
    ("临时居民身份证", "temp"), ("临时身份证", "temp"), ("临时证", "temp"),
    ("换领", "renew"),
)
BRANCH = re.compile("|".join(re.escape(word) for word, _ in sorted(BRANCH_WORDS, key=lambda w: -len(w[0]))))
BRANCH_ID = dict(BRANCH_WORDS)

# Attributes a number can belong to. Generic words such as 费用 or 收费 are compatible with any fee.
ATTRIBUTE_WORDS = (
    ("工本费", "fee.card"), ("证件费", "fee.card"),
    ("邮费", "fee.post"), ("邮寄费", "fee.post"), ("快递费", "fee.post"), ("寄递费", "fee.post"),
    ("办理时限", "time.process"), ("办结", "time.process"), ("时限", "time.process"), ("制证", "time.process"),
    ("有效期限", "time.valid"), ("有效期", "time.valid"),
    ("年龄", "age"), ("周岁", "age"),
)
ATTRIBUTE = re.compile("|".join(re.escape(word) for word, _ in sorted(ATTRIBUTE_WORDS, key=lambda w: -len(w[0]))))
ATTRIBUTE_ID = dict(ATTRIBUTE_WORDS)
# An unnamed amount in this corpus is the certificate fee; any other attribute must be named.
DEFAULT_ATTRIBUTE = {"元": "fee.card", "万元": "fee.card"}

RESTRICTION = ("有条件", "条件的", "仅限", "只限", "仅对", "仅为", "仅适用", "限于", "除外", "例外", "符合条件", "经批准", "原则上")
GENERALIZING = ("所有", "一律", "全部", "任何", "均可", "都可以", "都能", "一定", "保证", "必然", "无条件", "随时")
# Polarity pairs: (words in the quote, words in the claim, words that keep the claim faithful).
# Added after the held-out comparison; see evaluation/refactor-bench-20261115.
POLARITY = (
    ("OBLIGATION_STATED_AS_OPTIONAL", ("应当", "必须", "须", "需要"), ("可以不", "无需", "无须", "不必", "不用", "不需", "可不"), ()),
    ("WAIVER_STATED_AS_CHARGE", ("免收", "免费", "免予", "免交", "不收取", "停征", "免缴"), ("收取", "收费", "缴纳", "交纳"),
     ("免", "不收", "停征", "不再")),
    ("CHARGE_STATED_AS_WAIVER", ("收取", "收费", "缴纳", "交纳"), ("免收", "免费", "免予", "免交", "不收取", "停征", "免缴"), ()),
)
NEGATION = ("不得", "禁止", "不能", "不可", "不予", "严禁", "不允许", "不准", "无须", "无需", "不需")
PERMISSION = ("可以", "允许", "准予", "可由", "可委托", "能够")


def ids(pattern, table, text):
    return {table[match.group()] for match in pattern.finditer(text)}


def compatible(a, b):
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def conflicts(claimed, quoted):
    """True when both sides name branches (or attributes) and none of them agree."""
    return bool(claimed and quoted) and not any(compatible(a, b) for a in claimed for b in quoted)


def clause_spans(text, end=CLAUSE_END):
    spans, start = [], 0
    for match in end.finditer(text):
        if text[start:match.start()].strip():
            spans.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def quote_clauses(quote):
    """Clauses of a quote with the branch, attribute and restriction context that applies to each.

    A clause that names no branch takes the branches named earlier in the same sentence
    ("首次申领居民身份证的，收取工本费20元"); attributes are read the same way.
    """
    result = []
    for s_start, s_end in clause_spans(quote, SENTENCE_END):
        sentence = quote[s_start:s_end]
        branches, attributes = set(), set()
        for c_start, c_end in clause_spans(sentence, QUOTE_CLAUSE_END):
            clause = sentence[c_start:c_end]
            own_branches, own_attributes = ids(BRANCH, BRANCH_ID, clause), ids(ATTRIBUTE, ATTRIBUTE_ID, clause)
            branches = own_branches or branches
            attributes = own_attributes or attributes
            result.append({"text": clause, "sentence": sentence, "quantities": quantities(clause),
                           "branches": set(branches), "attributes": set(attributes)})
    return result


def attribute_conflict(claimed, quoted, unit):
    if not claimed:
        return False
    if not quoted:  # an unnamed number: only the default attribute of its unit is implied
        default = DEFAULT_ATTRIBUTE.get(unit)
        return not any(compatible(a, default) for a in claimed) if default else False
    return conflicts(claimed, quoted)


def binding_failures(index, claim, quote):
    """Failure codes for one claim against its own quote (both display text)."""
    failures = []
    clauses = quote_clauses(quote)
    claim_branches_all = ids(BRANCH, BRANCH_ID, claim)
    for c_start, c_end in clause_spans(claim):
        part = claim[c_start:c_end]
        branches = ids(BRANCH, BRANCH_ID, part) or (claim_branches_all if len(claim_branches_all) == 1 else set())
        attributes = ids(ATTRIBUTE, ATTRIBUTE_ID, part)
        for value, unit in sorted(quantities(part)):
            holding = [c for c in clauses if (value, unit) in c["quantities"]]
            if not holding:
                failures.append(f"A{index}_QUANTITY_NOT_IN_OWN_QUOTE:{value}{unit}")
                continue
            if all(conflicts(branches, c["branches"]) for c in holding):
                failures.append(f"A{index}_BRANCH_MISMATCH:{value}{unit}")
            elif all(attribute_conflict(attributes, c["attributes"], unit) for c in holding):
                failures.append(f"A{index}_ATTRIBUTE_MISMATCH:{value}{unit}")
    if any(word in quote for word in RESTRICTION) and not any(word in claim for word in RESTRICTION):
        failures.append(f"A{index}_RESTRICTION_DROPPED")
    if any(word in claim and word not in quote for word in GENERALIZING):
        failures.append(f"A{index}_GENERALIZED_BEYOND_QUOTE")
    if (any(word in quote for word in NEGATION) and not any(word in quote for word in PERMISSION)
            and any(word in claim for word in PERMISSION) and not any(word in claim for word in NEGATION)):
        failures.append(f"A{index}_PROHIBITION_STATED_AS_PERMISSION")
    for code, in_quote, in_claim, keeps in POLARITY:
        if (any(word in quote for word in in_quote) and any(word in claim for word in in_claim)
                and not any(word in quote for word in in_claim) and not any(word in claim for word in keeps)):
            failures.append(f"A{index}_{code}")
    return failures


# ---------------------------------------------------------------- document numbers and years
LEAD = re.compile(r"^(?:依据|根据|按照|参照|执行|印发|转发|下发|发布|关于|即|按|见|和|及|与|或|的|文件|通知|规定|办法|意见|号文|文|系|为|是|由)+")


def document_number_key(text):
    """Full issuer, year and number; brackets, spaces and leading words such as 依据 are ignored."""
    match = re.fullmatch(r"(?P<issuer>[一-鿿]+)[〔\[［(（](?P<year>\d{4})[〕\]］)）](?P<number>\d+)号", compact(text))
    if match is None:
        return compact(text)
    issuer = LEAD.sub("", match["issuer"]) or match["issuer"]
    return f"{issuer}〔{match['year']}〕{match['number']}号"


def document_number_failures(output, units_by_id):
    """A document number in a claim must be quoted by its own source; in the answer, by any cited source."""
    quoted, failures = {}, []
    for item in output["assertions"]:
        unit = units_by_id.get((item["source_id"], item["locator"]))
        if unit is not None and compact(item["quote"]) and compact(item["quote"]) in compact(unit["text"]):
            for match in DOCUMENT_NUMBER.finditer(item["quote"]):
                quoted.setdefault(item["source_id"], set()).add(document_number_key(match.group()))
    everything = set().union(*quoted.values()) if quoted else set()
    for index, item in enumerate(output["assertions"], 1):
        for match in DOCUMENT_NUMBER.finditer(item["claim"]):
            if document_number_key(match.group()) not in quoted.get(item["source_id"], set()):
                failures.append(f"A{index}_DOCUMENT_NUMBER_NOT_QUOTED:{document_number_key(match.group())}")
    for match in DOCUMENT_NUMBER.finditer(output["answer"]):
        if document_number_key(match.group()) not in everything:
            failures.append(f"ANSWER_DOCUMENT_NUMBER_NOT_QUOTED:{document_number_key(match.group())}")
    return failures


def title_years(units):
    """Years printed in each source's own title or document heading."""
    years = {}
    for unit in units:
        if unit.get("evidence_kind") == "source_title" or DOCUMENT_HEADING.fullmatch(unit["text"]):
            found = set(YEAR.findall(unit["text"]))
            if found:
                years.setdefault(unit["source_id"], set()).update(found)
    return years


ROLE_AFTER_YEAR = re.compile(rf"\s*(?:的)?\s*(?:{DATE_ROLE})")
EVENT_ACTOR = re.compile(
    r"\s*[,，]?\s*(?P<actor>[\u4e00-\u9fff]{2,14}(?:部门|机关|政府|机构|单位|委员会|公安|法院|学校))"
    r"\s*(?:将|通过|已|曾|开始|开展|实施|完成|把)"
)


def quoted_event_year(item, match, year):
    """An explicitly quoted year + actor + action dates an event, not the source document.

    The actor and year must both occur in this claim's own quote; a bare matching year
    elsewhere in the source does not excuse a wrong document title year.
    """
    event = EVENT_ACTOR.match(item["claim"], match.end())
    if event is None:
        return False
    quoted = rf"{year}\s*年\s*[,，]?\s*{re.escape(event['actor'])}\s*(?:将|通过|已|曾|开始|开展|实施|完成|把)"
    return bool(re.search(quoted, item["quote"]))


def document_year_failures(output, units):
    """A year naming a document (2026年通知) must be that document's title or heading year, if it prints one.

    A page publication year may still be stated as such ("2026年发布"); it does not make
    the document a 2026 document.
    """
    years, failures = title_years(units), []
    for index, item in enumerate(output["assertions"], 1):
        known = years.get(item["source_id"])
        if not known:
            continue
        for match in DOCUMENT_YEAR.finditer(item["claim"]):
            year = YEAR.search(match.group()).group()
            if (year not in known and not ROLE_AFTER_YEAR.match(item["claim"], match.end())
                    and not quoted_event_year(item, match, year)):
                failures.append(f"A{index}_DOCUMENT_YEAR_NOT_TITLE_YEAR:{year}")
    return failures


# ---------------------------------------------------------------- excerpts
def find_loose(text, quote):
    """(start, end) of ``quote`` in ``text`` ignoring whitespace differences, or None."""
    chars = [re.escape(char) for char in quote if not char.isspace()]
    if not chars:
        return None
    match = re.search(r"\s*".join(chars), text)
    return (match.start(), match.end()) if match else None


# ---------------------------------------------------------------- unknowns shown with a final
DIGITS = re.compile(r"\d")


def displayable_unknowns(unknowns):
    """Missing-item notes without numbers or quantities; a note is no place for an unchecked fact."""
    kept = [text for text in unknowns if not DIGITS.search(text) and not quantities(text)]
    hidden = len(unknowns) - len(kept)
    return kept, hidden

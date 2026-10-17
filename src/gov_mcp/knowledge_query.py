"""Question/source matching signals only; no material obligation or eligibility inference."""

import re
import unicodedata

from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_classify import TOPICS


QUERY_CUES = {
    "materials": ("材料", "要带", "带哪些", "带什么", "提交什么", "户口簿", "原件", "复印件"),
    "capture": ("照片", "相片", "拍照", "照相", "人像", "指纹", "照片回执"),
    "fee": ("费用", "收费", "多少钱", "交多少", "工本费", "缴费", "交费", "减免", "免费"),
    "duration": ("多久", "多长时间", "多少天", "几天", "工作日", "自然日", "时限", "办理周期"),
    "location": ("办理点", "受理点", "办理地点", "办理地址", "哪里办", "哪儿办", "到哪", "哪个窗口", "哪家派出所"),
    "delivery": ("领取", "领新证", "领证", "代领", "取证", "邮寄", "寄送", "凭证", "回执"),
    "privacy": ("个人信息", "信息安全", "隐私", "泄露", "保密", "发给你", "上传给你", "交给你"),
    "verification": ("核验", "核对哪些信息", "核对信息", "身份核查"),
    "renewal_window": ("提前", "期满前", "到期前", "尚未到期", "还没到期"),
    "effectivity": ("生效", "施行", "废止", "发布日期", "实施日期", "哪天实施", "明年才实施"),
}
SOURCE_EXTRA_CUES = {
    "materials": ("纸质材料", "申领登记表"),
    "fee": ("减收", "免收", "元/证", "元每证"),
    "duration": ("自然日", "办理周期", "办理期限", "压缩居民身份证办理时限"),
    "location": ("派出所", "户政窗口", "户籍窗口", "服务中心", "省内通办", "办证点"),
    "privacy": ("居民身份证信息", "公民个人信息", "不得扣押", "不得泄露"),
}
WINDOW_PATTERN = r"(?:期满|到期|届满).{0,18}(?:[三六36]个?月)|换领期限.{0,24}(?:[三六36]个?月)"
CONSULTATION_PATTERN = r"(?:咨询|发给你|上传给你|交给你)"
IDENTITY_DATA_PATTERN = r"身份证(?:号|号码|照片)|个人信息|证件号"
VERSION = "question-source-signals-v1-" + fingerprint([QUERY_CUES, SOURCE_EXTRA_CUES, TOPICS,
    WINDOW_PATTERN, CONSULTATION_PATTERN, IDENTITY_DATA_PATTERN])[:12]


def signal_text(text):
    """Derived view only. Never use its offsets or joined words as source quotations."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def question_signals(question):
    text = signal_text(question)
    topics = {topic for topic, cues in QUERY_CUES.items() if any(cue in text for cue in cues)}
    consultation_privacy = bool(re.search(CONSULTATION_PATTERN, text) and re.search(IDENTITY_DATA_PATTERN, text))
    if consultation_privacy:
        topics.add("privacy")
    collection = (any(term in text for term in ("代领", "邮寄", "送达", "领证", "领取新证", "领新证"))
        or ("领取" in text and any(term in text for term in ("新证", "窗口")) and "申请领取" not in text))
    if consultation_privacy or "privacy" in topics:
        primary = "privacy"
    elif "renewal_window" in topics:
        primary = "renewal_window"
    elif "duration" in topics:
        primary = "duration"
    elif "location" in topics and not collection:
        primary = "location"
    elif collection:
        primary = "delivery"
    else:
        concrete = topics-{"delivery", "effectivity"}
        primary = next(iter(concrete)) if len(concrete) == 1 else None
    return {"topics": topics, "primary": primary, "collection": collection}


def source_topics(text):
    normalized = signal_text(text)
    topics = {code for code, _, terms in TOPICS if code != "eligibility" and any(term in normalized for term in terms)}
    topics.update(topic for topic, terms in SOURCE_EXTRA_CUES.items() if any(term in normalized for term in terms))
    if re.search(WINDOW_PATTERN, normalized):
        topics.add("renewal_window")
    if any(term in normalized for term in ("核验", "核对", "查验")) and any(term in normalized for term in ("信息", "姓名", "身份号码")):
        topics.add("verification")
    return topics


OLD_DOCUMENT_PATTERN = r"旧身份证|旧证|原身份证|原居民身份证"
PRIVACY_ACTION_PATTERN = r"不得擅自(?:记录|复印|扫描)|严格设定查询权限|控制知悉范围"
PRIVACY_RULE_PATTERN = r"保密|隐私|信息安全|信息泄露|不得擅自(?:记录|复印|扫描)|不得扣押|不得扣留"
CLAUSE_SPLIT_PATTERN = r"[，,；;。！？!?]+"
V2_VERSION = "question-source-signals-v2-" + fingerprint([VERSION, OLD_DOCUMENT_PATTERN,
    PRIVACY_ACTION_PATTERN, PRIVACY_RULE_PATTERN, CLAUSE_SPLIT_PATTERN])[:12]


def question_signals_v2(question):
    value = question_signals(question)
    text = signal_text(question)
    if re.search(OLD_DOCUMENT_PATTERN, text) and any(term in text for term in ("交回", "交验", "提交", "收走", "收回", "核验", "核对")):
        value["topics"].add("document_handling")
        if value["primary"] != "privacy":
            value["primary"] = "document_handling"
    return value


def source_topics_v2(text):
    value = source_topics(text)
    normalized = signal_text(text)
    # A reference to the personal-information side of a card is not a privacy rule.
    if "privacy" in value and not re.search(PRIVACY_RULE_PATTERN, normalized):
        value.discard("privacy")
    if re.search(PRIVACY_ACTION_PATTERN, normalized):
        value.add("privacy")
    if "核查" in normalized and any(term in normalized for term in ("身份证", "信息", "人证一致")):
        value.add("verification")
    if re.search(OLD_DOCUMENT_PATTERN, normalized) or "交验居民身份证" in normalized:
        value.add("document_handling")
    return value


def question_demands(question):
    """Distinct literal topic demands; this is not a semantic completeness judgment."""
    demands, seen = [], set()
    for clause in re.split(CLAUSE_SPLIT_PATTERN, question):
        value = question_signals_v2(clause)
        signature = (frozenset(value["topics"]), value["primary"])
        if value["topics"] and signature not in seen:
            seen.add(signature)
            demands.append(clause)
    return demands


def intent_source_key(question, unit, identity_context=False):
    signals = question_signals_v2(question)
    text = signal_text(unit["text_for_ranking"])
    topics = source_topics_v2(text)
    priority = len(signals["topics"] & topics)
    if signals["primary"] in topics:
        priority += len(signals["topics"])+1
    if signals["primary"] == "privacy" and re.search(PRIVACY_ACTION_PATTERN, text):
        priority += len(signals["topics"])+1
    phase = 0
    if signals["collection"] and signals["primary"] == "delivery":
        from gov_mcp.knowledge_structure import structure
        hints = structure(text)
        kinds = {m["kind"] for m in hints["markers"]}
        if not hints["truncated"] and "phase:collection" in kinds:
            phase = 1 if "phase:application" in kinds else 2
    title = signal_text(unit.get("source_title") or "")
    subject = int(identity_context and "身份证" in text+title)
    lead = re.split(r"[。；;]", text, maxsplit=1)[0]
    branch = -1 if identity_context and "临时居民身份证" in lead and "临时" not in signal_text(question) else 0
    from gov_mcp.knowledge_retrieval import query_grams, score_literal
    grams = query_grams(question)
    return phase, priority, subject, branch, score_literal(grams, title), score_literal(grams, unit["text_for_ranking"])


DOCUMENT_ACTION_PATTERN = r"交回(?:原证|旧证)|(?:交验|提交|收回|收走).{0,6}(?:原|旧)(?:居民)?身份证|交验居民身份证"
MINOR_FOCUS_PATTERN = r"未满(?:十六|16)周岁|未成年人"
NONLOCAL_FOCUS_PATTERN = r"异地办证|异地受理|跨户籍所在地"
QUANTIFIED_DAYS_PATTERN = r"[零〇一二两三四五六七八九十百千0-9]+个?(?:自然|工作)?日"
V3_VERSION = "question-source-signals-v3.1-" + fingerprint([V2_VERSION, DOCUMENT_ACTION_PATTERN,
    MINOR_FOCUS_PATTERN, NONLOCAL_FOCUS_PATTERN, QUANTIFIED_DAYS_PATTERN, "explicit_actions_and_time_units_v2"])[:12]


def source_topics_v3(text):
    value = source_topics_v2(text)
    normalized = signal_text(text)
    if re.search(DOCUMENT_ACTION_PATTERN, normalized):
        value.add("document_handling")
    else:
        value.discard("document_handling")
    if re.search(OLD_DOCUMENT_PATTERN, normalized):
        value.add("materials")
    if "时限" in normalized:
        value.add("duration")
    return value


def intent_source_key_v3(question, unit, identity_context=False):
    signals = question_signals_v2(question)
    text, query = signal_text(unit["text_for_ranking"]), signal_text(question)
    topics = source_topics_v3(text)
    phase, _, subject, branch, title_score, literal_score = intent_source_key(question, unit, identity_context)
    priority = len(signals["topics"] & topics)
    if signals["primary"] in topics:
        priority += len(signals["topics"])+1
    if signals["primary"] == "privacy" and re.search(PRIVACY_ACTION_PATTERN, text):
        priority += len(signals["topics"])+1
    lead = re.split(r"[。；;]", text, maxsplit=1)[0]
    if "成年" in query and "未成年" not in query and re.search(MINOR_FOCUS_PATTERN, lead):
        branch -= 1
    if "本地户籍" in query and "非本地户籍" not in query and re.search(NONLOCAL_FOCUS_PATTERN, lead):
        branch -= 1
    time_units = sum(term in query and term in text for term in ("自然日", "工作日")) if "duration" in signals["topics"] else 0
    quantified_days = int(bool(re.search(QUANTIFIED_DAYS_PATTERN, text))) if "duration" in signals["topics"] else 0
    return phase, priority, subject, branch, time_units, quantified_days, title_score, literal_score

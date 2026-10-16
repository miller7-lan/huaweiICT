"""Conservative recognition of branches excluded from the agreed pilot."""

import re


SUBJECT = r"(?:(?:我|其|他)的)?(?:家属|亲属|别人|他人|朋友|配偶|父母|父亲|母亲|爸爸|妈妈|子女|哥哥|姐姐|丈夫|妻子)"
ANCHOR = r"(?:代办|代理(?:人)?|代为|委托(?!书)|(?:让|请)"+SUBJECT+r"|帮我|替我)"
# Do not scan past an earlier collection action or an information/preparation request.
BRIDGE = r"(?:(?!申请|申领|换领|代领|领取|领证|代取|取证|拿证|邮寄|送达|(?:和|与|或|或者|还是|以及)(?:代领|代取|领取|领证)|解释|查询|查找|说明|整理|准备|填写|复印|打印|核对|检查|翻译|[，,、。；;！？?!]).){0,16}?"
APPLICATION = r"(?:提交申请|递交申请|申请(?!表|材料|流程|指南|说明|条件|要求|政策)|申领(?!登记表|材料|流程|指南)|换领(?!材料|流程|指南|说明|条件|要求)|办证|办理申请|办(?:理)?(?:居民)?身份证)"
COLLECTION = r"(?:代领|代取|领取|领证|取证|拿证|[领取拿](?:回)?(?:新证|居民身份证|身份证|证件))"
APPLICATION_REQUEST = re.compile(ANCHOR+BRIDGE+APPLICATION)
APPLICATION_OBJECT = re.compile(r"(?:代办|代理办理|代为办理)(?:居民)?身份证(?!的?(?:领取|领证|取证))")
COLLECTION_REQUEST = re.compile(ANCHOR+BRIDGE+COLLECTION+r"|代领|代取|代为领取|代理领取|代为领证|代理领证")
UNSPECIFIED_PROXY = re.compile(r"代办|代理|代为|委托(?!书)|(?:让|请)"+SUBJECT)
RECEIPT_OBJECT = re.compile(r"(?:代领|代取|代为领取|代理领取)(?:的)?(?:居民身份证)?(?:领取凭证|凭证|回执|申领登记表|登记表|申请表|申请材料)")
NOMINAL_APPLICATION = re.compile(r"(?:代理|代为|代办)(?:领取|领证)(?:居民身份证|身份证|新证)?的?申请")


def _explicitly_negated(text, start):
    before = text[max(0, start-6):start]
    return before.endswith(("不是", "并非", "不属于", "不涉及")) and not before.endswith(("是不是", "是否不是"))


def _excluded_nonproxy_branch(question):
    exclusions = (
        ("temporary_id", ("临时居民身份证", "临时身份证")),
        ("nonlocal_household", ("异地", "外地户籍", "外省", "外市户籍", "非本地户籍", "不是本地户籍", "户籍不在", "户籍在其他")),
        ("minor", ("未成年", "不满十八", "未满十八", "不满18", "未满18", "十八岁以下", "18岁以下")),
        ("lost_or_damaged", ("补领", "丢失", "遗失", "损坏", "损毁")),
        ("changed_identity_data", ("姓名变更", "变更姓名", "改名", "性别变更", "民族变更")),
    )
    for code, terms in exclusions:
        if any(term in question for term in terms):
            return code
    # A stated minor age may be written without the word "minor".
    for digits in re.findall(r"(?<!\d)(\d{1,2})(?:周)?岁", question):
        if int(digits) < 18:
            return "minor"
    for chinese in re.findall(r"([零〇一二两三四五六七八九十]{1,3})(?:周)?岁", question):
        if chinese in {"一", "二", "两", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七"}:
            return "minor"
    return None


def scope_decision(question):
    """Recognize the requested stage, not administrative eligibility or permission."""
    if not isinstance(question, str) or not question.strip() or len(question) > 300:
        raise ValueError("Question must contain 1-300 characters")
    text = re.sub(r"\s+", "", question)
    branch = _excluded_nonproxy_branch(text)
    if branch:
        return {"excluded_branch": branch, "proxy_stage_clarification_required": False}
    ignored = []
    for match in [*APPLICATION_REQUEST.finditer(text), *APPLICATION_OBJECT.finditer(text)]:
        if _explicitly_negated(text, match.start()):
            ignored.append(match.span())
        else:
            return {"excluded_branch": "proxy", "proxy_stage_clarification_required": False}
    collections = list(COLLECTION_REQUEST.finditer(text))
    # The application receipt/form is not the finished identity card.
    if any(not _explicitly_negated(text, m.start()) for pattern in (RECEIPT_OBJECT, NOMINAL_APPLICATION) for m in pattern.finditer(text)):
        return {"excluded_branch": None, "proxy_stage_clarification_required": True}
    masked = list(text)
    for start, end in [*ignored, *(match.span() for match in collections)]:
        masked[start:end] = " "*(end-start)
    for match in UNSPECIFIED_PROXY.finditer(text):
        if _explicitly_negated(text, match.start()):
            masked[match.start():match.end()] = " "*(match.end()-match.start())
    unclear = bool(UNSPECIFIED_PROXY.search("".join(masked)))
    return {"excluded_branch": None, "proxy_stage_clarification_required": unclear}


def excluded_branch(question):
    """Legacy conservative boolean-style use; ambiguous delegation remains blocked."""
    decision = scope_decision(question)
    return decision["excluded_branch"] or ("proxy_stage_unclear" if decision["proxy_stage_clarification_required"] else None)

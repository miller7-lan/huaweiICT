"""Conservative recognition of branches excluded from the agreed pilot."""

import re


def excluded_branch(question):
    exclusions = (
        ("temporary_id", ("临时居民身份证", "临时身份证")),
        ("nonlocal_household", ("异地", "外地户籍", "外省", "外市户籍", "非本地户籍", "不是本地户籍", "户籍不在", "户籍在其他")),
        ("minor", ("未成年", "不满十八", "未满十八", "不满18", "未满18", "十八岁以下", "18岁以下")),
        ("lost_or_damaged", ("补领", "丢失", "遗失", "损坏", "损毁")),
        ("proxy", ("代办", "代理", "代领", "委托家属", "委托别人", "委托他人")),
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

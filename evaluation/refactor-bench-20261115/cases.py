"""Synthetic cases for comparing verification designs offline. No real corpus text, no real
question, no personal data and no model call: every unit, draft and question below is made up
in the style of the frozen Henan ID-card corpus.

Each draft case gives one stored-style model draft and what may or may not be displayed:
- kind "bad": none of ``must_not_show`` may appear in what the flow displays;
- kind "good": the flow must display an answer (not a stop) that contains all of ``must_show``.
Gate cases give a question and the gate code it must produce (None: must pass).
"""
from hashlib import sha256


def unit(source, locator, text, kind="html_paragraph", **extra):
    return {"source_id": source, "code": source, "locator": locator, "text": text,
            "text_sha256": sha256(text.encode()).hexdigest(), "evidence_kind": kind, **extra}


def draft(answer, *assertions, outcome="answer", reason="", unknowns=()):
    return {"outcome": outcome, "answer": answer, "stop_reason": reason, "unknowns": list(unknowns),
            "assertions": [dict(zip(("claim", "source_id", "locator", "quote"), a)) for a in assertions]}


FEE = unit("S-FEE", "正文段落1", "首次申领居民身份证收取工本费20元；损坏换领居民身份证收取工本费40元。")
TEMP = unit("S-TEMP", "正文段落1", "临时居民身份证工本费10元。")
AGENT = unit("S-AGENT", "正文段落1", "申领居民身份证必须本人办理，不得代办。")
COND = unit("S-COND", "正文段落1", "有条件的测试窗口，可以当天制作发放测试证。")
PHOTO = unit("S-PHOTO", "正文段落1", "照片不合格的，可重新拍照三次。")
VALID = unit("S-VALID", "正文段落1", "临时居民身份证有效期限为三个月。")
BASIS = unit("S-BASIS", "正文段落1", "依据财综[2007]34号收取工本费。")
T_TITLE = unit("S-YEAR", "网页标题", "2018年测试通知", "source_title")
T_DATE = unit("S-YEAR", "网页发布日期", "发布日期：2026-03-01", "page_publication_date", calendar_date="2026-03-01")
T_BODY = unit("S-YEAR", "正文段落1", "测试事项工本费20元。")
DAYS = unit("S-DAYS", "正文段落1", "办理时限为三个工作日。")
RENEW = unit("S-RENEW", "正文段落1", "到期换领居民身份证的，工本费20元，办理时限15个工作日。")
ROW = unit("S-XLSX", "工作表1第36行", "测试事项 | 20元", "xlsx_row", cells=["F36", "G36"])
LONG = unit("S-LONG", "正文段落1", "为方便群众办理，各地应当公布办理地点和咨询电话，并在醒目位置张贴办事指南，"
            "做好政策解释和便民服务工作，不得以任何理由推诿。" * 2
            + "仅对材料齐全的申请人，有条件的窗口收取工本费 20元。其他事项按规定执行。")
POST = unit("S-POST", "正文段落1", "申请人可以选择邮寄送达，邮费由申请人自理。")
FIRST = unit("S-FIRST", "正文段落1", "首次申领居民身份证的，收取工本费20元。")

UNITS = [FEE, TEMP, AGENT, COND, PHOTO, VALID, BASIS, T_TITLE, T_DATE, T_BODY, DAYS, RENEW, ROW, LONG, POST, FIRST]

Q = "测试问题（合成）"
FEE_Q = FEE["text"]
DRAFT_CASES = [
    # F01 numbers swapped between branches of one quote
    {"id": "F01_branch_swap", "issue": "F01", "kind": "bad", "must_not_show": ["首次申领工本费40元", "损坏换领工本费20元"],
     "draft": draft("首次申领工本费40元，损坏换领工本费20元，临时证10元。",
                    ("首次申领工本费40元", "S-FEE", "正文段落1", FEE_Q),
                    ("损坏换领工本费20元", "S-FEE", "正文段落1", FEE_Q),
                    ("临时居民身份证工本费10元", "S-TEMP", "正文段落1", TEMP["text"]))},
    {"id": "F01_control", "issue": "F01", "kind": "good", "must_show": ["首次申领工本费20元", "损坏换领工本费40元", "10元"],
     "draft": draft("首次申领20元，损坏换领40元，临时证10元。",
                    ("首次申领工本费20元", "S-FEE", "正文段落1", FEE_Q),
                    ("损坏换领工本费40元", "S-FEE", "正文段落1", FEE_Q),
                    ("临时居民身份证工本费10元", "S-TEMP", "正文段落1", TEMP["text"]))},
    {"id": "F01_list_swap", "issue": "F01", "kind": "bad", "must_not_show": ["首次申领40元"],
     "draft": draft("见下。", ("首次申领40元、损坏换领20元", "S-FEE", "正文段落1", FEE_Q))},
    # F02 summary adds a contrary conclusion
    {"id": "F02_summary_permission", "issue": "F02", "kind": "bad", "must_not_show": ["可以委托他人代办"],
     "draft": draft("可以委托他人代办。", ("必须本人办理，不得代办", "S-AGENT", "正文段落1", AGENT["text"]))},
    {"id": "F02_same_amount_summary", "issue": "F02", "kind": "bad", "must_not_show": ["均免收"],
     "draft": draft("两项均免收。", ("首次申领工本费20元", "S-FIRST", "正文段落1", FIRST["text"]),
                    ("测试事项工本费20元", "S-YEAR", "正文段落1", T_BODY["text"]))},
    {"id": "F02_control", "issue": "F02", "kind": "good", "must_show": ["不得代办"],
     "draft": draft("必须本人办理，不得代办。", ("必须本人办理，不得代办", "S-AGENT", "正文段落1", AGENT["text"]))},
    # F03 restriction dropped
    {"id": "F03_condition_dropped", "issue": "F03", "kind": "bad", "must_not_show": ["所有测试窗口均可当天领取"],
     "draft": draft("所有测试窗口均可当天领取测试证。",
                    ("所有测试窗口均可当天领取测试证", "S-COND", "正文段落1", COND["text"]))},
    {"id": "F03_condition_dropped_plain", "issue": "F03", "kind": "bad", "must_not_show": ["测试窗口可以当天制作发放测试证"],
     "draft": draft("测试窗口可以当天制作发放测试证。",
                    ("测试窗口可以当天制作发放测试证", "S-COND", "正文段落1", COND["text"]))},
    {"id": "F03_control", "issue": "F03", "kind": "good", "must_show": ["有条件的测试窗口"],
     "draft": draft("有条件的测试窗口可以当天制作发放测试证。",
                    ("有条件的测试窗口可以当天制作发放测试证", "S-COND", "正文段落1", COND["text"]))},
    # F04 Chinese counts and months
    {"id": "F04_count", "issue": "F04", "kind": "bad", "must_not_show": ["九次"],
     "draft": draft("可重新拍照九次。", ("照片不合格的可重新拍照九次", "S-PHOTO", "正文段落1", PHOTO["text"]))},
    {"id": "F04_months", "issue": "F04", "kind": "bad", "must_not_show": ["九个月"],
     "draft": draft("有效期限九个月。", ("临时居民身份证有效期限为九个月", "S-VALID", "正文段落1", VALID["text"]))},
    {"id": "F04_months_arabic", "issue": "F04", "kind": "bad", "must_not_show": ["9个月"],
     "draft": draft("有效期限9个月。", ("临时居民身份证有效期限为9个月", "S-VALID", "正文段落1", VALID["text"]))},
    {"id": "F04_control", "issue": "F04", "kind": "good", "must_show": ["3个月"],
     "draft": draft("有效期限3个月。", ("临时居民身份证有效期限为3个月", "S-VALID", "正文段落1", VALID["text"]))},
    # F05 document number issuer
    {"id": "F05_wrong_issuer", "issue": "F05", "kind": "bad", "must_not_show": ["豫财综"],
     "draft": draft("依据豫财综〔2007〕34号收取工本费。",
                    ("依据豫财综〔2007〕34号收取工本费", "S-BASIS", "正文段落1", BASIS["text"]))},
    {"id": "F05_control_brackets", "issue": "F05", "kind": "good", "must_show": ["财综〔2007〕34号"],
     "draft": draft("依据财综〔2007〕34号收取工本费。",
                    ("依据财综〔2007〕34号收取工本费", "S-BASIS", "正文段落1", BASIS["text"]))},
    # F06 page publication year used as document year
    {"id": "F06_page_year", "issue": "F06", "kind": "bad", "must_not_show": ["2026年通知"],
     "draft": draft("2026年通知规定测试事项工本费20元。",
                    ("2026年通知规定测试事项工本费20元", "S-YEAR", "正文段落1", T_BODY["text"]),
                    ("网页发布日期为2026-03-01", "S-YEAR", "网页发布日期", T_DATE["text"]))},
    {"id": "F06_control", "issue": "F06", "kind": "good", "must_show": ["2018年通知"],
     "draft": draft("2018年通知规定测试事项工本费20元。",
                    ("2018年通知规定测试事项工本费20元", "S-YEAR", "正文段落1", T_BODY["text"]),
                    ("2018年测试通知", "S-YEAR", "网页标题", T_TITLE["text"]))},
    # F10 numeral rewrites that keep the value
    {"id": "F10_cn_to_arabic", "issue": "F10", "kind": "good", "must_show": ["3个工作日"],
     "draft": draft("办理时限3个工作日。", ("办理时限为3个工作日", "S-DAYS", "正文段落1", DAYS["text"]))},
    {"id": "F10_arabic_to_cn", "issue": "F10", "kind": "good", "must_show": ["二十元"],
     "draft": draft("首次申领工本费二十元。", ("首次申领工本费二十元", "S-FIRST", "正文段落1", FIRST["text"]))},
    {"id": "F10_value_changed", "issue": "F10", "kind": "bad", "must_not_show": ["9个工作日"],
     "draft": draft("办理时限9个工作日。", ("办理时限为9个工作日", "S-DAYS", "正文段落1", DAYS["text"]))},
    {"id": "F10_unit_changed", "issue": "F10", "kind": "bad", "must_not_show": ["15个自然日"],
     "draft": draft("到期换领办理时限15个自然日。", ("到期换领办理时限15个自然日", "S-RENEW", "正文段落1", RENEW["text"]))},
    # F11 cell address
    {"id": "F11_cell_address", "issue": "F11", "kind": "good", "must_show": ["G36"],
     "draft": draft("G36记载测试事项工本费20元。", ("G36记载测试事项工本费20元", "S-XLSX", "工作表1第36行", ROW["text"]))},
    {"id": "F11_unknown_cell", "issue": "F11", "kind": "bad", "must_not_show": ["G99"],
     "draft": draft("G99记载测试事项工本费20元。", ("G99记载测试事项工本费20元", "S-XLSX", "工作表1第36行", ROW["text"]))},
    # F12 excerpt must keep the sentence's condition
    {"id": "F12_excerpt_condition", "issue": "F12", "kind": "bad",
     "must_not_show": ["原文摘录：工本费 20元", "原文摘录：工本费20元", "30元"],
     "draft": draft("工本费30元；邮寄送达邮费自理。",
                    ("工本费30元", "S-LONG", "正文段落1", "工本费 20元"),
                    ("申请人可以选择邮寄送达，邮费由申请人自理", "S-POST", "正文段落1", POST["text"]))},
    # F15 stop explanation states an unchecked amount
    {"id": "F15_stop_amount", "issue": "F15", "kind": "bad", "must_not_show": ["60元"],
     "draft": draft("测试事项费用为60元。", outcome="stop", reason="缺少其他依据，但测试事项费用为60元",
                    unknowns=["测试事项费用为60元"])},
    # Attribute and source borrowing beyond the audit
    {"id": "X_attribute_borrowed", "issue": "X", "kind": "bad", "must_not_show": ["邮费10元"],
     "draft": draft("邮费10元。", ("邮费10元", "S-TEMP", "正文段落1", TEMP["text"]))},
    {"id": "X_cross_source_borrow", "issue": "X", "kind": "bad", "must_not_show": ["临时证工本费20元"],
     "draft": draft("临时证工本费20元。", ("临时证工本费20元", "S-TEMP", "正文段落1", TEMP["text"]))},
    {"id": "X_generic_branch_control", "issue": "X", "kind": "good", "must_show": ["换领工本费20元"],
     "draft": draft("换领工本费20元。", ("换领工本费20元", "S-RENEW", "正文段落1", RENEW["text"]))},
    {"id": "X_subject_earlier_clause", "issue": "X", "kind": "good", "must_show": ["首次申领收取工本费20元"],
     "draft": draft("首次申领收取工本费20元。", ("首次申领收取工本费20元", "S-FIRST", "正文段落1", FIRST["text"]))},
    {"id": "X_no_subject_control", "issue": "X", "kind": "good", "must_show": ["工本费20元"],
     "draft": draft("工本费20元。", ("工本费20元", "S-RENEW", "正文段落1", RENEW["text"]))},
    {"id": "X_two_facts_control", "issue": "X", "kind": "good", "must_show": ["15个工作日"],
     "draft": draft("到期换领工本费20元，办理时限15个工作日。",
                    ("到期换领工本费20元，办理时限15个工作日", "S-RENEW", "正文段落1", RENEW["text"]))},
    {"id": "X_cross_doc_control", "issue": "X", "kind": "good", "must_show": ["10元", "20元"],
     "draft": draft("临时证10元，首次申领20元。",
                    ("临时居民身份证工本费10元", "S-TEMP", "正文段落1", TEMP["text"]),
                    ("首次申领居民身份证收取工本费20元", "S-FIRST", "正文段落1", FIRST["text"]))},
    {"id": "X_invalid_quote", "issue": "X", "kind": "bad", "must_not_show": ["工本费5元"],
     "draft": draft("工本费5元。", ("首次申领工本费5元", "S-FIRST", "正文段落1", "首次申领工本费5元"))},
]

# F13 is about the label, not the text: an unanswered short item must not be labelled complete.
GAP_CASES = [
    {"id": "F13_short_gap", "issue": "F13", "expect_kind": "answer_with_gaps",
     "draft": draft("首次申领工本费20元。", ("首次申领工本费20元", "S-FIRST", "正文段落1", FIRST["text"]), unknowns=["邮费"])},
    {"id": "F13_long_gap", "issue": "F13", "expect_kind": "answer_with_gaps",
     "draft": draft("首次申领工本费20元。", ("首次申领工本费20元", "S-FIRST", "正文段落1", FIRST["text"]),
                    unknowns=["邮费金额"])},
]

SYNTHETIC_ID = "1234562000010100"  # 16 digits; two more are appended below, never a real number
GATE_CASES = [
    ("F07_spaced_id", "F07", f"证件号{SYNTHETIC_ID[:6]} {SYNTHETIC_ID[6:14]} {SYNTHETIC_ID[14:]}12能换领吗", "PRIVATE_FACT_NOT_ACCEPTED"),
    ("F07_dashed_id_x", "F07", f"证件号{SYNTHETIC_ID[:6]}-{SYNTHETIC_ID[6:14]}-{SYNTHETIC_ID[14:]}1X能办吗", "PRIVATE_FACT_NOT_ACCEPTED"),
    ("F07_plain_id", "F07", f"证件号{SYNTHETIC_ID}12能办吗", "PRIVATE_FACT_NOT_ACCEPTED"),
    ("F07_doc_numbers_pass", "F07", "财综[2007]34号和豫公〔2021〕16号分别规定了什么？", None),
    ("F07_phone_pass", "F07", "咨询电话0371-12345678是哪个窗口？", None),
    ("F08_outside_city", "F08", "杭州居民换领身份证多少钱？", "OUTSIDE_HENAN_SCOPE"),
    ("F08_outside_city2", "F08", "深圳办理临时身份证要多久？", "OUTSIDE_HENAN_SCOPE"),
    ("F08_province", "F08", "江苏换领身份证收费多少？", "OUTSIDE_HENAN_SCOPE"),
    ("F08_henan_city_pass", "F08", "郑州换领居民身份证工本费是多少？", None),
    ("F08_henan_city_pass2", "F08", "南阳和信阳的居民补领身份证要带什么？", None),
    ("F08_henan_county_pass", "F08", "固始县居民在永城办理换领需要多久？", None),
    ("F09_still_valid", "F09", "这项规定还有效吗？", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("F09_abolished", "F09", "这个收费标准是否已废止？", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("F09_still_follow", "F09", "还能照此办理吗？", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("F09_now", "F09", "现在换领要多少钱？", "CURRENT_VALIDITY_NOT_ESTABLISHED"),
    ("F09_disclaimer_pass", "F09", "无需判断当前是否有效，只解释归档中的收费记载。", None),
    ("F09_history_pass", "F09", "归档资料记载该通知何时废止？", None),
    ("F09_validity_period_pass", "F09", "临时居民身份证有效期限是多少？", None),
    ("F09_valid_document_pass", "F09", "换领需要带有效户口簿吗？", None),
    ("F14_negated_region_pass", "F14", "只读归档，不涉及江苏，只问河南身份证换领的归档收费。", None),
    ("F14_excluded_list_pass", "F14", "与江苏、浙江无关，只问河南补领时限。", None),
    ("F14_compare_stop", "F14", "河南与江苏的换领收费有什么不同？", "OUTSIDE_HENAN_SCOPE"),
    ("F14_nanjing_stop", "F14", "不涉及收费，南京实际办理要多久？", "OUTSIDE_HENAN_SCOPE"),
]

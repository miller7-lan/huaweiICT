"""Held-out synthetic cases, written after candidate A was built and not used to tune it.

They use other wordings than cases.py: natural paraphrases that should pass, and quieter
errors (dropped conditions outside the lexicons, flipped obligations, wrong starting points)
that a lexical design may miss. Every text is synthetic.
"""
from cases import draft, unit

H = {
    "LIST": unit("H-LIST", "正文段落1", "居民身份证工本费标准为：首次申领、换领每证20元，补领每证40元。"),
    "ISSUE": unit("H-ISSUE", "正文段落1", "受理后，公安机关应当在60日内发放居民身份证；交通不便的地区，可以适当延长，但最长不超过30日。"),
    "DOCS": unit("H-DOCS", "正文段落1", "申请人应当提交户口簿和近期免冠照片。"),
    "TERMS": unit("H-TERMS", "正文段落1", "居民身份证有效期分为五年、十年、二十年和长期四种。"),
    "AGE": unit("H-AGE", "正文段落1", "年满十六周岁的公民，应当申领居民身份证。"),
    "MAIL": unit("H-MAIL", "正文段落1", "对符合条件的申请人，可以通过邮寄方式送达居民身份证。"),
    "TEMP": unit("H-TEMP", "正文段落1", "临时居民身份证的有效期限为三个月，有效期限自签发之日起计算。"),
    "TIME": unit("H-TIME", "正文段落1", "办理时限：10个工作日（不含邮寄时间）。"),
    "FREE": unit("H-FREE", "正文段落1", "因公安机关原因造成居民身份证损坏的，免费换领。"),
    "POSTFEE": unit("H-POSTFEE", "正文段落1", "工本费20元，邮寄费用由申请人承担。"),
    "WAIVE": unit("H-WAIVE", "正文段落1", "首次申领居民身份证免收工本费。"),
    "STAY": unit("H-STAY", "正文段落1", "在本地居住满一年的，可以申请办理居住证。"),
}
UNITS = list(H.values())


def c(case_id, kind, key, claim, check):
    item = H[key]
    field = "must_show" if kind == "good" else "must_not_show"
    return {"id": case_id, "issue": "holdout", "kind": kind, field: [check],
            "draft": draft(claim, (claim, item["source_id"], item["locator"], item["text"]))}


CASES = [
    c("H01_reissue_fee", "good", "LIST", "补领每证40元", "补领每证40元"),
    c("H02_first_fee_in_list", "good", "LIST", "首次申领每证20元", "首次申领每证20元"),
    c("H03_issue_time", "good", "ISSUE", "一般在60日内发放，交通不便地区可适当延长，最长不超过30日", "最长不超过30日"),
    c("H04_documents", "good", "DOCS", "需提交户口簿和近期免冠照片", "户口簿"),
    c("H05_terms_arabic", "good", "TERMS", "有效期有5年、10年、20年和长期四种", "20年"),
    c("H06_age", "good", "AGE", "年满16周岁的公民应当申领居民身份证", "16周岁"),
    c("H07_mail_condition", "good", "MAIL", "符合条件的申请人可以选择邮寄送达", "符合条件"),
    c("H08_temp_term", "good", "TEMP", "临时居民身份证有效期3个月，自签发之日起算", "3个月"),
    c("H09_time_note", "good", "TIME", "办理时限10个工作日，不含邮寄时间", "10个工作日"),
    c("H10_free_renew", "good", "FREE", "因公安机关原因造成损坏的可以免费换领", "免费换领"),
    c("H11_stay", "good", "STAY", "在本地居住满1年的可以申请居住证", "1年"),
    c("H12_post_fee_payer", "good", "POSTFEE", "邮寄费用由申请人承担，工本费20元", "工本费20元"),
    c("B01_list_swap", "bad", "LIST", "补领每证20元", "补领每证20元"),
    c("B02_extension_number", "bad", "ISSUE", "最长可延长60日", "最长可延长60日"),
    c("B03_mail_everyone", "bad", "MAIL", "所有申请人都可以邮寄送达", "所有申请人都可以邮寄送达"),
    c("B04_terms_number", "bad", "TERMS", "有效期分为五年、十年和三十年", "三十年"),
    c("B05_age_number", "bad", "AGE", "年满十八周岁的公民应当申领居民身份证", "十八周岁"),
    c("B06_condition_outside_lexicon", "bad", "FREE", "居民身份证损坏的可以免费换领", "居民身份证损坏的可以免费换领"),
    c("B07_obligation_flipped", "bad", "DOCS", "申请人可以不提交户口簿", "可以不提交户口簿"),
    c("B08_post_fee_amount", "bad", "POSTFEE", "邮寄费用20元", "邮寄费用20元"),
    c("B09_wrong_start", "bad", "TEMP", "有效期限三个月，自领取之日起计算", "自领取之日起计算"),
    c("B10_unit_swap", "bad", "TIME", "办理时限10个自然日", "10个自然日"),
    c("B11_waiver_flipped", "bad", "WAIVE", "首次申领居民身份证收取工本费", "首次申领居民身份证收取工本费"),
    c("B12_branch_swap_in_list", "bad", "LIST", "换领每证40元", "换领每证40元"),
]

"""Automatic faithful and perturbed drafts from a small synthetic corpus.

For every fact sentence, a faithful claim restates one clause (good), and perturbations
change one thing the claim must not change (bad): the number, the unit, the branch, the
attribute, a dropped restriction, or a generalization. Numeral rewrites that keep the value
are good. Every sentence is synthetic, in the style of the frozen corpus.
"""
from cases import draft, unit

# (source, sentence, [(faithful claim, perturbations: list of (kind, claim)), ...], numeral rewrites)
SENTENCES = [
    ("P01", "首次申领居民身份证的，收取工本费20元；到期换领的，收取工本费20元。", [
        ("首次申领工本费20元", [("number", "首次申领工本费25元"), ("attribute", "首次申领邮费20元")]),
    ], [("首次申领工本费二十元", True)]),
    ("P02", "损坏换领居民身份证收取工本费40元，遗失补领居民身份证收取工本费40元。", [
        ("损坏换领工本费40元", [("number", "损坏换领工本费20元"), ("branch", "首次申领工本费40元")]),
        ("遗失补领工本费40元", [("branch", "临时居民身份证工本费40元")]),
    ], [("遗失补领工本费四十元", True)]),
    ("P03", "临时居民身份证工本费10元，有效期限为三个月。", [
        ("临时居民身份证工本费10元", [("number", "临时居民身份证工本费12元"), ("attribute", "临时居民身份证邮费10元")]),
        ("临时居民身份证有效期限为三个月", [("number", "临时居民身份证有效期限为六个月"), ("attribute", "临时居民身份证办理时限为三个月")]),
    ], [("临时居民身份证有效期限为3个月", True)]),
    ("P04", "首次申领居民身份证的，办理时限为15个工作日；遗失补领的，办理时限为20个工作日。", [
        ("首次申领办理时限为15个工作日", [("number", "首次申领办理时限为10个工作日"), ("unit", "首次申领办理时限为15个自然日"),
                                    ("branch", "遗失补领办理时限为15个工作日")]),
        ("遗失补领办理时限为20个工作日", [("branch", "首次申领办理时限为20个工作日")]),
    ], [("首次申领办理时限为十五个工作日", True)]),
    ("P05", "仅限本省户籍居民，可以在省内异地申请换领居民身份证。", [
        ("仅限本省户籍居民可以在省内异地申请换领居民身份证",
         [("restriction", "居民可以在省内异地申请换领居民身份证"), ("generalize", "所有居民均可在省内异地申请换领居民身份证")]),
    ], []),
    ("P06", "符合条件的窗口，可以当天制作发放临时居民身份证。", [
        ("符合条件的窗口可以当天制作发放临时居民身份证",
         [("restriction", "窗口可以当天制作发放临时居民身份证"), ("generalize", "所有窗口一律当天发放临时居民身份证")]),
    ], []),
    ("P07", "申领居民身份证应当由本人到场，不得委托他人代办。", [
        ("申领居民身份证应当由本人到场，不得委托他人代办", [("negation", "申领居民身份证可以委托他人代办")]),
    ], []),
    ("P08", "照片不合格的，可重新拍照三次；仍不合格的，不予受理。", [
        ("照片不合格的可重新拍照三次", [("number", "照片不合格的可重新拍照五次")]),
    ], [("照片不合格的可重新拍照3次", True)]),
    ("P09", "未满十六周岁的公民，自愿申领居民身份证的，有效期为五年。", [
        ("未满十六周岁的公民申领的居民身份证有效期为五年", [("number", "未满十六周岁的公民申领的居民身份证有效期为十年"),
                                          ("attribute", "未满十六周岁的公民申领的居民身份证办理时限为五年")]),
    ], [("未满16周岁的公民申领的居民身份证有效期为5年", True)]),
    ("P10", "遗失补领居民身份证的，应当在挂失后30日内申请。", [
        ("遗失补领应当在挂失后30日内申请", [("number", "遗失补领应当在挂失后60日内申请"), ("unit", "遗失补领应当在挂失后30个工作日内申请")]),
    ], [("遗失补领应当在挂失后三十日内申请", True)]),
]


def generated():
    units, cases = [], []
    for source, text, facts, rewrites in SENTENCES:
        item = unit(source, "正文段落1", text)
        units.append(item)
        for faithful, perturbations in facts:
            cases.append({"id": f"{source}_faithful_{len(cases)}", "issue": "auto-faithful", "kind": "good",
                          "must_show": [faithful], "draft": draft(faithful, (faithful, source, "正文段落1", text))})
            for kind, claim in perturbations:
                cases.append({"id": f"{source}_{kind}_{len(cases)}", "issue": f"auto-{kind}", "kind": "bad",
                              "must_not_show": [claim], "draft": draft(claim, (claim, source, "正文段落1", text))})
        for claim, _ in rewrites:
            cases.append({"id": f"{source}_rewrite_{len(cases)}", "issue": "auto-rewrite", "kind": "good",
                          "must_show": [claim], "draft": draft(claim, (claim, source, "正文段落1", text))})
    return units, cases

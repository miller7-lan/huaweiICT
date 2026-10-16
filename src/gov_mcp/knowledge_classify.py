"""Auditable candidate labels and mentioned branches; no inference of mandatory requirements."""

from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_structure import VERSION as STRUCTURE_VERSION, structure


TOPICS = (
    ("materials", "材料与提交形式", ("户口簿", "提交材料", "交验", "原件", "复印件", "证明材料")),
    ("capture", "照片与指纹", ("人像", "照片", "相片", "照相", "指纹")),
    ("fee", "费用", ("收费", "工本费", "免费", "费用")),
    ("duration", "时限", ("工作日", "日内", "办理时限", "办理期限")),
    ("location", "办理地点与渠道", ("办理地点", "办理地址", "受理点", "服务窗口", "公安派出所", "网上办理")),
    ("delivery", "领取与送达", ("领取", "邮寄", "送达", "领证", "回执")),
    ("eligibility", "适用条件", ("年满", "未满", "户籍", "常住户口", "有效期满", "到期", "届满")),
    ("privacy", "使用与信息保护", ("个人信息", "信息安全", "泄露", "隐私", "保密", "不得扣押")),
    ("effectivity", "效力与日期", ("施行", "实施日期", "废止", "有效性", "自发布之日")),
)
BRANCHES = (
    ("expiry_renewal", "到期换领", ("到期", "有效期满", "届满")),
    ("first_issue", "首次申领", ("首次申领", "首次申请领取")),
    ("minor", "未成年人", ("未成年", "未满十六", "监护人")),
    ("nonlocal", "异地办理", ("异地", "户口所在地以外", "非本地")),
    ("lost_or_damaged", "丢失或损坏", ("丢失", "补领", "损坏", "损毁")),
    ("proxy", "代办或代领", ("代办", "代理", "委托", "代为领取")),
    ("temporary_id", "临时身份证", ("临时居民身份证", "临时身份证")),
    ("changed_identity", "登记项目变更", ("姓名变更", "变更姓名", "登记项目变更")),
)
VERSION = "condition-proposals-v2-" + fingerprint([TOPICS, BRANCHES, STRUCTURE_VERSION])[:12]


def suggest(chunks, metadata):
    sections = []
    for chunk in chunks:
        text = chunk["text"]
        topics = [{"code": code, "label": label, "matched_terms": [term for term in terms if term in text]}
                  for code, label, terms in TOPICS if any(term in text for term in terms)]
        branches = [{"code": code, "label": label, "matched_terms": [term for term in terms if term in text]}
                    for code, label, terms in BRANCHES if any(term in text for term in terms)]
        instruction_flag = any(term in text for term in ("忽略系统规则", "忽略之前的指令", "自动发布所有", "ignore previous instructions"))
        sections.append({"locator": chunk["locator"], "topics": topics, "mentioned_branches": branches,
                         "instruction_like_text_requires_review": instruction_flag,
                         "source_structure": structure(text),
                         "material_requirement_relationship": "unknown_pending_human_review"})
    return {"taxonomy_version": VERSION, "status": "machine_proposal_not_approved",
        "scope_from_source_config_not_inferred_from_host": {"region": metadata["region"], "matter": metadata["matter"]},
        "topic_codes": sorted({topic["code"] for section in sections for topic in section["topics"]}),
        "mixed_branch_mentions": len({branch["code"] for section in sections for branch in section["mentioned_branches"]}) > 1,
        "sections": sections,
        "warning": "Mentions and keyword matches do not establish applicability, required materials, current effectivity or a review decision"}

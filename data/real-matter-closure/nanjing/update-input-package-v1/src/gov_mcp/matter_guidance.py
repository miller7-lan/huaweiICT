"""Literal, scope-checked consultation sections for all sixteen real-source rules.

This module neither decides actual applicant eligibility nor upgrades a source
description into a reviewed policy. It never calls a model or government service.
"""
from datetime import datetime, timedelta, timezone

from gov_mcp.matter_graph import MatterProofError, scope_check

TOPICS = {
    "overview": {"label": "办理概览", "rules": [f"NR{i:02}" for i in range(1, 17)]},
    "materials": {"label": "材料与例外", "rules": ["NR05", "NR06", "NR07"]},
    "online": {"label": "现场办理与网办限制", "rules": ["NR03", "NR04"]},
    "window": {"label": "提前办理窗口", "rules": ["NR01", "NR02"]},
    "time": {"label": "服务周期与法定期限", "rules": ["NR08", "NR09", "NR14"]},
    "fee": {"label": "工本费与邮费", "rules": ["NR10", "NR11"]},
    "collection": {"label": "领取新证与旧证交回", "rules": ["NR11", "NR12", "NR13"]},
    "temporary": {"label": "急需用证与临时证路径", "rules": ["NR09", "NR15"]},
    "progress": {"label": "进度查询指引", "rules": ["NR16"]},
    "old_card_lifecycle": {"label": "旧证使用与交回的跨文档推导", "rules": ["NR12", "NR13"]},
}
UNDECIDED = {
    "overview": ["减免资格", "具体领取日期", "临时证办理细则"],
    "materials": ["特殊资格的实际材料核定"],
    "window": ["实际到期日与精确最早受理日"],
    "time": ["精确计时起点", "实际领取日期", "考生加急具体天数"],
    "fee": ["困难减免资格和证明", "具体邮费金额"],
    "collection": ["具体邮费金额", "现场证件核验结果"],
    "temporary": ["临时证异地资格", "临时证材料、费用与时限"],
}


def guidance(pack, facts, topic, *, now):
    if topic not in TOPICS or topic == "old_card_lifecycle":
        raise MatterProofError("未知来源查询主题")
    gate = scope_check(pack, facts, require_old_card_valid=False)
    if gate["status"] != "IN_SCOPE":
        return {**gate, "knowledge_published": False, "formal_business_answer": False}
    today = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).date().isoformat()
    sources = {s["source_id"]: s for s in pack["sources"]}
    citations = {c["id"]: c for c in pack["citations"]}
    segments = {row["segment_id"]: row for s in pack["sources"] for row in s["segments"]}
    rules = {r["id"]: r for r in pack["rules"]}
    if len(rules) != len(pack["rules"]):
        raise MatterProofError("规则编号重复")
    sections = []
    for rule_id in TOPICS[topic]["rules"]:
        rule = rules.get(rule_id)
        if not rule:
            raise MatterProofError("主题所需规则缺失")
        refs = []
        for key in rule["citation_ids"]:
            ref = citations[key]
            source = sources[ref["source_id"]]
            if source.get("published_at") and source["published_at"] > today:
                raise MatterProofError("来源公布日期晚于当前查询日期")
            if source.get("effective_from") and source["effective_from"] > today:
                raise MatterProofError("来源尚未施行")
            refs.append({**ref, "url": source["url"], "title": source["title"],
                "published_at": source["published_at"], "effective_from": source["effective_from"],
                "original_sha256": source["original_sha256"],
                "locator": segments[ref["segment_id"]]["locator"],
                "full_context": segments[ref["segment_id"]]["text"]})
        section = {"rule_id": rule_id, "title": rule["title"],
            "source_statement": rule["required_text"], "conditions": rule["conditions"],
            "scope_note": rule["note"], "citations": refs, "review_status": "pending_human_review",
            "applicability": "exception_context" if rule["predicate"] in
                ("outprovince_extra_proof", "guardian_exception") else "source_description"}
        if rule["predicate"] == "advance_window_months":
            start, end = rule["conditions"]["source_service_period"]
            if not start <= today < end:
                section["applicability"] = "source_service_period_outside_current_date"
        if rule["predicate"] == "old_card_usable_during_processing":
            if facts.get("old_card_valid") is not True:
                section["applicability"] = "needs_validity_clarification" if facts.get("old_card_valid") is None else "condition_not_met"
        if rule["predicate"] == "standard_fee_cny" and facts.get("fee_relief_requested") is True:
            section["applicability"] = "fee_relief_requires_separate_review"
        sections.append(section)
    return {"status": "SOURCE_GUIDANCE_PENDING_REVIEW", "topic": topic, "topic_label": TOPICS[topic]["label"],
        "as_of": today, "sections": sections, "not_decided": UNDECIDED.get(topic, []),
        "human_review": pack["human_review"]["status"], "knowledge_published": False,
        "formal_business_answer": False, "limits": [
            "仅展示所选官方来源的记载及适用边界，规则仍待人工复核。",
            "例外上下文不累计为主分支材料，未知资格不自动放行。"]}

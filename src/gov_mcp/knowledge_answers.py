"""Literal presentation of verified, published search results; no policy inference.

Ranges use Python Unicode character offsets within an extracted parent chunk,
not byte offsets or positions in a PDF. The store verifies originals and review
digests before binding; these hashes are integrity identifiers, not signatures.
"""

import hashlib
import json
import re


FIELDS = ("revision_id", "title", "url", "locator", "category", "quote",
          "published_at", "effective_from", "effective_to", "knowledge_version",
          "original_sha256", "chunk_ordinal")


def bind_citation(source):
    if not isinstance(source, dict) or any(key not in source for key in FIELDS):
        raise ValueError("Incomplete citation")
    value = {key: source[key] for key in FIELDS}
    if any(not isinstance(value[key], str) or not value[key] for key in
           ("revision_id", "title", "url", "locator", "category", "quote", "knowledge_version")):
        raise ValueError("Incomplete citation")
    if not isinstance(value["original_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["original_sha256"]):
        raise ValueError("Original fingerprint required")
    if type(value["chunk_ordinal"]) is not int or value["chunk_ordinal"] < 0:
        raise ValueError("Parent chunk ordinal required")
    value["text_sha256"] = hashlib.sha256(value["quote"].encode("utf-8")).hexdigest()
    value["source_span"] = {"basis": "extracted_parent_chunk", "unit": "unicode_character",
                            "start": 0, "end": len(value["quote"])}
    value["citation_id"] = "kb-" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return value


def select_citations(catalog, version, selected_ids=None):
    """Select exact bound IDs only. Never accept replacement quotes or ranges."""
    indexed = {}
    for citation in catalog:
        if citation != bind_citation(citation) or citation["knowledge_version"] != version:
            raise ValueError("Changed or cross-version citation")
        key = citation["citation_id"]
        if key in indexed:
            raise ValueError("Duplicate catalog citation")
        indexed[key] = citation
    ids = list(indexed) if selected_ids is None else selected_ids
    if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
        raise ValueError("Citation IDs only")
    if len(set(ids)) != len(ids) or any(key not in indexed for key in ids):
        raise ValueError("Unknown or duplicate citation ID")
    return [dict(indexed[key], source_span=dict(indexed[key]["source_span"])) for key in ids]


STOP_MESSAGES = {
    "NO_PUBLISHED_KNOWLEDGE": "当前没有已发布且可引用的知识库版本，暂不能据此给出办理结论。",
    "NO_SUPPORTING_EVIDENCE": "所查询的已发布快照中没有匹配的依据，暂不能据此给出办理结论。",
    "UPDATE_PENDING_REVIEW": "发现待复核更新，已暂停引用当前版本；复核并发布后再提供依据。",
    "UPDATE_AWAITING_PUBLICATION": "资料变更尚未发布，已暂停引用当前版本。",
    "SOURCE_CHECK_REQUIRED": "来源检查未通过或已过期，已暂停引用当前版本。",
    "OUT_OF_CONFIRMED_SCOPE": "问题超出已确认的试点范围，暂不能提供该分支的办理结论。",
    "SCOPE_CLARIFICATION_REQUIRED": "请说明他人参与的是申请办理、材料协助，还是本人办理申请后的新证领取环节；无需提供身份证号码。",
    "PERSONAL_DATA_NOT_ALLOWED": "请移除身份证号码、手机号或邮箱后再提问。",
    "INVALID_QUESTION": "请输入1至300字的问题。",
    "KNOWLEDGE_READER_UNAVAILABLE": "暂时无法核对知识库依据，请稍后重试。",
}


def render_answer(value):
    """Format full original chunks with distinct publication/effect/query dates."""
    if value["status"] != "OK":
        if value["citations"] or value["items"]:
            raise ValueError("Stopped answer must not contain evidence")
        return STOP_MESSAGES.get(value["message_code"], "当前无法提供已核对的办理依据。")
    citations = select_citations(value["citations"], value["knowledge_version"])
    if not citations or value["message_code"] != "EVIDENCE_FOUND":
        raise ValueError("Supporting evidence required")
    data = value["data"]
    lines = ["工作人员核对依据" if data["role"] == "staff" else "办事咨询依据",
             "以下为检索命中的完整原文片段；材料组合及办理结论须结合各条适用条件核对。",
             "知识库版本：" + value["knowledge_version"], "查询日期：" + data["query_date"]]
    if data["historical_snapshot"]:
        lines.append("历史知识库快照：不代表当前发布版本。")
    if data["query_view"] == "past":
        lines.append("历史日期筛选：依据所选快照中记录的生效区间，不重建当时的网站状态。")
    elif data["query_view"] == "future":
        lines.append("未来日期筛选：仅依据所选快照已知的生效区间，不保证未来仍然有效。")
    for citation in citations:
        lines.extend(["", "引用：" + citation["citation_id"], "文件：" + citation["title"],
            "来源：" + citation["url"], "定位：" + citation["locator"],
            "发布日期：" + (citation["published_at"] or "未确认"),
            "生效日期：" + (citation["effective_from"] or "未确认"),
            "失效日期：" + (citation["effective_to"] or "未记录"),
            "原文：", citation["quote"]])
    lines.extend(["", "以上为依据检索结果，不构成审批决定。"])
    return "\n".join(lines)

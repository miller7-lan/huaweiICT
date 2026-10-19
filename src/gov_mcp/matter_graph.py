"""Source-bound relation candidates and bounded join proofs for public development.

No provider calls, policy publication, human approval, or arbitrary code execution.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from gov_mcp.knowledge_fetch import parse_html


class MatterProofError(ValueError):
    pass


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_child(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise MatterProofError("来源路径无效或越界")
    return target


def load_pack(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if file_hash(safe_child(root, item["path"]).read_bytes()) != item["sha256"]:
            raise MatterProofError("交付包文件指纹变化：" + item["path"])
    raw = (root / "rules-and-sources.json").read_bytes()
    if file_hash(raw) != manifest["rules_sha256"]:
        raise MatterProofError("规则包指纹变化")
    pack = json.loads(raw)
    sources, segments = {}, {}
    for source in pack["sources"]:
        if source["source_id"] in sources:
            raise MatterProofError("来源编号重复")
        original = safe_child(root, source["original_file"]).read_bytes()
        if file_hash(original) != source["original_sha256"]:
            raise MatterProofError("官方原件指纹变化")
        rows, _ = parse_html(original, source["selector"], min_text_length=1)
        for segment in source["segments"]:
            match = [r for r in rows if r["locator"] == segment["locator"]]
            if (len(match) != 1 or match[0]["text"] != segment["text"]
                    or file_hash(segment["text"].encode()) != segment["text_sha256"]):
                raise MatterProofError("原件段落与展示文字不一致")
            key = segment["segment_id"]
            if key in segments:
                raise MatterProofError("段落编号重复")
            segments[key] = {**segment, "source_id": source["source_id"]}
        sources[source["source_id"]] = source
    for citation in pack["citations"]:
        segment = segments.get(citation["segment_id"])
        if not segment or citation["source_id"] != segment["source_id"]:
            raise MatterProofError("引用来源绑定失效")
        text = segment["text"]
        start, end = citation["start"], citation["end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != citation["quote"]
                or file_hash(text.encode()) != citation["context_sha256"]):
            raise MatterProofError("引用偏移或上下文失效")
    return pack


# A small transparent seed. New relationship candidates remain proposals.
SEED = {
    "version": "temporal-seed-v1",
    "entity_types": {"old_card": "Credential", "processing": "Phase", "collection": "Phase"},
    "relations": {"usable_during": ["Credential", "Phase"], "precedes": ["Phase", "Phase"]},
    "axioms": [{"triple": ["processing", "precedes", "collection"],
                "kind": "workflow_phase_order_not_policy_claim"}],
}
PATTERNS = (
    {"id": "SEED-USE", "regex": r"申请换领居民身份证期间，未超期的旧身份证可以继续使用",
     "triple": ["old_card", "usable_during", "processing"], "guard": {"old_card_valid": True},
     "relation_type": ["Credential", "Phase"], "role": "seed_known_relation"},
    {"id": "PROPOSE-RETURN", "regex": r"领取新证时，必须交回原证",
     "triple": ["old_card", "return_at", "collection"], "guard": {},
     "relation_type": ["Credential", "Phase"], "role": "new_relation_proposal"},
)
COMPOSITION = {
    "id": "TEMPORAL-COMPOSE",
    "body": [["?card", "usable_during", "?before"], ["?before", "precedes", "?after"],
             ["?card", "return_at", "?after"]],
    "head": ["?card", "usable_until_return", "?after"],
    "description": "同一证件在前阶段可用，后阶段须交回，生成使用至交回阶段的时序关系",
}


def extract_candidates(pack):
    candidates = []
    for source in pack["sources"]:
        if source["region"] not in ("南京市", "national"):
            continue
        for segment in source["segments"]:
            for pattern in PATTERNS:
                for match in re.finditer(pattern["regex"], segment["text"]):
                    proof = {"source_id": source["source_id"], "source_region": source["region"],
                        "url": source["url"], "original_sha256": source["original_sha256"],
                        "segment_id": segment["segment_id"], "locator": segment["locator"],
                        "start": match.start(), "end": match.end(), "quote": match.group(),
                        "context_sha256": segment["text_sha256"]}
                    candidate = {"triple": pattern["triple"], "guard": pattern["guard"],
                        "relation_type": pattern["relation_type"], "pattern_id": pattern["id"],
                        "role": pattern["role"], "evidence": [proof],
                        "review_status": "machine_proposed_pending_human"}
                    candidate["id"] = "REL-" + canonical_hash(candidate)[:20]
                    candidates.append(candidate)
    return {"method": "two_seed_patterns_not_llm_or_measured_low_resource_gain",
        "seed": SEED, "seed_pattern_count": len(PATTERNS), "manual_rule_cards_used_for_extraction": 0,
        "candidates": candidates, "human_review": "pending"}


def preview_ontology(proposals):
    ontology = deepcopy(SEED)
    ontology["version"] = "temporal-preview-v2-unpublished"
    ontology["relations"]["usable_until_return"] = ["Credential", "Phase"]
    for proposal in proposals["candidates"]:
        relation = proposal["triple"][1]
        if relation not in ontology["relations"]:
            ontology["relations"][relation] = proposal["relation_type"]
    ontology["inference_rules"] = [COMPOSITION]
    ontology["approval"] = "development_preview_not_published"
    return ontology


def scope_check(pack, facts):
    allowed = {"region", "matter", "adult", "civil_capacity", "self", "reason", "household", "old_card_valid"}
    if set(facts) - allowed:
        raise MatterProofError("只接受已定义的无个人身份信息字段")
    fixed = {"region": "南京市", "matter": "resident-id-expiry-renewal",
        "adult": True, "civil_capacity": "full", "self": True, "reason": "expiry", "household": "jiangsu"}
    missing = [k for k in fixed if k not in facts or facts[k] is None]
    missing += [k for k in ("old_card_valid",) if k not in facts or facts[k] is None]
    if missing:
        return {"status": "NEEDS_CLARIFICATION", "fields": sorted(set(missing))}
    if any(type(facts[k]) is not bool for k in ("adult", "self", "old_card_valid")):
        raise MatterProofError("布尔条件必须明确，不能按字符串或数字猜测")
    outside = [k for k, v in fixed.items() if facts[k] != v]
    if outside:
        return {"status": "OUT_OF_SCOPE", "fields": outside}
    return {"status": "IN_SCOPE"}


def match_triple(pattern, triple, bindings):
    result = dict(bindings)
    for term, value in zip(pattern, triple):
        if term.startswith("?"):
            if term in result and result[term] != value:
                return None
            result[term] = value
        elif term != value:
            return None
    return result


def prove(pack, facts, *, ontology=None, excluded_sources=()):
    """Compute a draft relation proof; evidence never grants publication permission."""
    gate = scope_check(pack, facts)
    if gate["status"] != "IN_SCOPE":
        return {**gate, "knowledge_published": False, "formal_business_answer": False}
    proposals = extract_candidates(pack)
    ontology = ontology or SEED
    excluded = set(excluded_sources)
    rows = []
    for axiom in ontology["axioms"]:
        rows.append({"triple": axiom["triple"], "evidence": [],
                     "proof": {"kind": "axiom", "basis": axiom["kind"]}})
    for candidate in proposals["candidates"]:
        subject, relation, target = candidate["triple"]
        if relation not in ontology["relations"]:
            continue
        if [ontology["entity_types"].get(subject), ontology["entity_types"].get(target)] != ontology["relations"][relation]:
            raise MatterProofError("本体关系类型不兼容")
        if any(facts.get(k) != v for k, v in candidate["guard"].items()):
            continue
        if any(e["source_id"] in excluded for e in candidate["evidence"]):
            continue
        rows.append({"triple": candidate["triple"], "evidence": candidate["evidence"],
                     "proof": {"kind": "source_candidate", "candidate_id": candidate["id"]}})
    for rule in ontology.get("inference_rules", []):
        if set(rule) - {"id", "body", "head", "description"} or len(rule["body"]) > 8:
            raise MatterProofError("推导规则超出固定声明式契约")
        if rule["head"][1] not in ontology["relations"]:
            raise MatterProofError("推导目标关系未定义")
        matches = [({}, [])]
        attempts = 0
        for pattern in rule["body"]:
            joined = []
            for bindings, inputs in matches:
                for row in rows:
                    attempts += 1
                    if attempts > 5000:
                        raise MatterProofError("推导连接预算已用完")
                    binding = match_triple(pattern, row["triple"], bindings)
                    if binding is not None:
                        joined.append((binding, inputs + [row]))
            matches = joined
        for bindings, inputs in matches:
            triple = [bindings.get(term, term) for term in rule["head"]]
            if any(term.startswith("?") for term in triple):
                raise MatterProofError("推导输出含未绑定变量")
            if [ontology["entity_types"].get(triple[0]), ontology["entity_types"].get(triple[2])] != ontology["relations"][triple[1]]:
                raise MatterProofError("推导结果类型不兼容")
            evidence = {canonical_hash(e): e for row in inputs for e in row["evidence"]}
            sources = sorted({e["source_id"] for e in evidence.values()})
            # The demonstration explicitly requires more than one source document.
            if len(sources) < 2:
                continue
            rows.append({"triple": triple, "evidence": list(evidence.values()), "source_ids": sources,
                "proof": {"kind": "join", "rule_id": rule["id"],
                    "inputs": [{"triple": row["triple"], "proof": row["proof"]} for row in inputs]}})
    conclusions = [row for row in rows if row["triple"][1] == "usable_until_return"]
    return {"status": "DRAFT_PROOF_READY" if conclusions else "NO_SUPPORTED_MULTI_DOCUMENT_PROOF",
        "ontology_version": ontology["version"], "proofs": conclusions,
        "source_relations": [row for row in rows if row["proof"]["kind"] == "source_candidate"],
        "knowledge_published": False, "formal_business_answer": False,
        "human_review": pack["human_review"]["status"],
        "limits": ["真实公开原文的开发推导；不是人工业务批准或独立评测。",
                   "缺少来源、条件或确认时不能直接发布为正式办事承诺。"]}

"""Literal source markers and offsets for review; never infer legal applicability."""

import re

from gov_mcp.knowledge import fingerprint


NUMBER = r"(?:\d{1,4}|[零〇一二两三四五六七八九十百千]+)"
PATTERNS = (
    ("branch:first_issue", r"申请领取的|初次申请领取|首次申领"),
    ("branch:renewal", r"申请换领的|申请换领|到期换领|有效期满"),
    ("branch:replacement", r"申请补领的|申请补领|丢失补领|损坏换领"),
    ("branch:minor", r"未满十六周岁|未满16周岁|未成年人|监护人"),
    ("phase:application", r"申请换领|申请补领|申请领取"),
    ("artifact:registration_form", r"申领登记表"),
    ("phase:collection", r"公民领取新证|领取新证时|领取新证|本人领取居民身份证|代为领取"),
    ("phase:inspection", r"交验"),
    ("phase:return", r"交回(?:原证|旧证)"),
    ("scope:household", r"办理户口业务过程中|户口业务"),
    ("purpose:relationship", r"关系证明|证明双方关系"),
    ("condition:shared_confirmation", r"能够通过数据共享|电子证照调用|不再要求群众提交纸质材料"),
    ("negation_or_prohibition", r"不得|不能|不予|不再|无需|不需要|不适用"),
    ("fee:exemption", r"(?:不得|不予|不再)?(?:免收工本费|减半征收工本费|可以减收工本费|减收工本费)"),
    ("duration", r"(?<![\d年月])"+NUMBER+r"(?:个)?(?:自然日|工作日|日内?|个月)"),
)
LIMITS = {"characters": 16000, "markers": 64, "brief_markers": 8}
VERSION = "literal-structure-v2-"+fingerprint([PATTERNS, LIMITS])[:12]


def structure(text):
    if not isinstance(text, str):
        raise ValueError("Source text required")
    body = text[:LIMITS["characters"]]
    truncated = len(body) != len(text)
    # Exclude explicit printed dates from duration hints; do not normalize dates.
    dates = [m.span() for m in re.finditer(
        NUMBER+r"年"+NUMBER+r"月"+NUMBER+r"日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:日)?", body)]
    markers = []
    for kind, pattern in PATTERNS:
        count = 0
        for match in re.finditer(pattern, body):
            if kind == "duration" and any(a <= match.start() < b for a, b in dates):
                continue
            count += 1
            if count > LIMITS["markers"]:
                truncated = True
                break
            markers.append({"kind": kind, "span": [match.start(), match.end()], "surface": match.group()})
    markers.sort(key=lambda item: (*item["span"], item["kind"]))
    if len(markers) > LIMITS["markers"]:
        truncated = True
        markers = markers[:LIMITS["markers"]]
    kinds = {item["kind"] for item in markers}
    cautions = []
    if len(kinds & {"branch:first_issue", "branch:renewal", "branch:replacement"}) > 1:
        cautions.append("do_not_merge_application_branches")
    if "phase:collection" in kinds:
        cautions.append("collection_is_not_application")
    if "scope:household" in kinds and "purpose:relationship" in kinds:
        cautions.append("household_relationship_proof_is_not_identity_material_substitution")
    if "condition:shared_confirmation" in kinds:
        cautions.append("paper_waiver_requires_source_conditions")
    if "fee:exemption" in kinds:
        cautions.append("do_not_make_universal_fee_claim")
    if "negation_or_prohibition" in kinds:
        cautions.append("preserve_negation_and_prohibition_context")
    if "duration" in kinds:
        cautions.append("keep_time_expressions_bound_to_their_source_and_conditions")
    return {"algorithm_version": VERSION, "status": "literal_hints_not_approved",
            "text_sha256": fingerprint(text.encode("utf-8")), "offset_unit": "unicode_code_points",
            "markers": markers, "cautions": cautions, "truncated": truncated,
            "applicability": "unknown", "material_obligations": "unknown",
            "current_effectivity": "unknown"}


def brief(text):
    """A bounded model hint, not a source rewrite or a reference answer."""
    full = structure(text)
    unique = {(m["kind"], m["surface"]) for m in full["markers"]}
    priority = {"branch:first_issue": 0, "branch:renewal": 0, "branch:replacement": 0,
                "negation_or_prohibition": 0,
                "scope:household": 1, "purpose:relationship": 1, "fee:exemption": 1,
                "duration": 1, "phase:collection": 2, "phase:return": 2,
                "condition:shared_confirmation": 2}
    selected = sorted(unique, key=lambda item: (priority.get(item[0], 3), item))[:LIMITS["brief_markers"]]
    return {"literal_markers": [{"kind": kind, "surface": value} for kind, value in selected],
            "cautions": full["cautions"], "hints_incomplete": full["truncated"] or len(selected) < len(unique),
            "status": full["status"]}

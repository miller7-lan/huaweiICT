from gov_mcp.knowledge import fingerprint
from gov_mcp.knowledge_classify import suggest
from gov_mcp.knowledge_structure import LIMITS, brief, structure


def test_branch_markers_keep_offsets_without_inventing_obligations():
    text = "申请领取的，交验甲簿；申请换领的，交验乙证；申请补领的，交验丙证之一。"
    value = structure(text)
    assert {m["surface"] for m in value["markers"] if m["kind"].startswith("branch:")} == {"申请领取的", "申请换领的", "申请补领的"}
    assert all(text[slice(*m["span"])] == m["surface"] for m in value["markers"])
    assert value["material_obligations"] == value["applicability"] == "unknown"
    assert "do_not_merge_application_branches" in value["cautions"]
    assert value["text_sha256"] == fingerprint(text.encode("utf-8"))


def test_relationship_proof_and_paper_waiver_remain_separate():
    text = "能够通过数据共享确认的，不再要求群众提交纸质材料。\n办理户口业务过程中，涉及关系证明的，可核对调查材料。"
    value = structure(text)
    assert "paper_waiver_requires_source_conditions" in value["cautions"]
    assert "household_relationship_proof_is_not_identity_material_substitution" in value["cautions"]
    assert "required_materials" not in value and "waived_materials" not in value


def test_collection_and_application_markers_do_not_become_same_stage():
    value = structure("申请换领的，交验证件。领取新证时，交回原证。")
    kinds = {m["kind"] for m in value["markers"]}
    assert {"phase:application", "phase:inspection", "phase:collection", "phase:return"} <= kinds
    assert "collection_is_not_application" in value["cautions"]


def test_fee_exceptions_are_not_flattened_to_universal_payment():
    value = structure("一般收工本费。对甲免收工本费，对乙可以减收工本费。")
    assert "do_not_make_universal_fee_claim" in value["cautions"]
    assert value["current_effectivity"] == "unknown"


def test_negated_relief_phrase_is_not_reduced_to_an_affirmative_marker():
    value = structure("某分支不得免收工本费，另一分支免收工本费。")
    assert [m["surface"] for m in value["markers"] if m["kind"] == "fee:exemption"] == ["不得免收工本费", "免收工本费"]
    assert "preserve_negation_and_prohibition_context" in value["cautions"]
    assert value["material_obligations"] == "unknown"


def test_dates_are_not_durations_and_distinct_limits_are_not_merged():
    text = "文件自2022年9月1日起施行，网页2024-10-17。六十日内发放，延长不得超过三十日。新措施为25个自然日。"
    value = structure(text)
    assert [m["surface"] for m in value["markers"] if m["kind"] == "duration"] == ["六十日内", "三十日", "25个自然日"]
    assert not any(key in value for key in ("minimum_days", "maximum_days", "effective_from"))


def test_ocr_spelling_is_kept_and_not_corrected_to_a_number():
    text = "OCR保留第八十ー条和20巧年；时限2O个自然日待核对。"
    value = structure(text)
    assert value["text_sha256"] == fingerprint(text.encode("utf-8"))
    assert not any(m["surface"] == "20个自然日" for m in value["markers"])


def test_oversized_source_or_marker_list_has_visible_limit_signal():
    value = structure("申请换领的，" * 5000)
    assert value["truncated"] and len(value["markers"]) <= LIMITS["markers"]
    hints = brief("申请领取的，甲；申请换领的，乙；申请补领的，丙。领取新证时交回原证。")
    assert len(hints["literal_markers"]) <= LIMITS["brief_markers"]
    assert "text" not in hints and hints["status"] == "literal_hints_not_approved"


def test_classification_retains_source_scope_and_unknown_material_relation():
    text = "申请领取的，交验甲簿；申请换领的，交验乙证。"
    proposal = suggest([{"locator": "自拟条款", "text": text, "category": "identity"}], {"region": "unknown", "matter": "unclassified"})
    section = proposal["sections"][0]
    assert proposal["status"] == "machine_proposal_not_approved"
    assert proposal["scope_from_source_config_not_inferred_from_host"]["region"] == "unknown"
    assert section["source_structure"]["markers"]
    assert section["material_requirement_relationship"] == "unknown_pending_human_review"

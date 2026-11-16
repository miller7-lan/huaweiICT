"""Region-isolation fix F1: a regional service refuses a corpus or answer outside its profile
(synthetic data; the audit's case M08 is a Henan fee mixed with a Nanjing unit)."""
import json

import pytest

from gov_mcp import frozen_qa, regional_scope
from gov_mcp.frozen_qa_flow_server import FlowService
from test_frozen_qa_flow_v3 import answer, unit

HENAN = unit("HN-A", "正文段落1", "河南归档：首次申领测试证工本费23元。")
NATIONAL = unit("MOF-X", "正文段落1", "国家归档：测试证工本费标准为23元。")
NANJING = unit("NJ-X", "正文段落1", "南京归档：首次申领测试证工本费99元。")


def profile(units):
    return {"profile": "test", "sources": {"HN-A": "henan", "MOF-X": "national"},
            "approved_corpus_unit_sha256": {frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True)): "t"}}


def corpus_sha(units):
    return frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True))


def test_the_committed_henan_profile_lists_seven_sources_and_three_corpora():
    loaded = regional_scope.load_profile()
    roles = sorted(loaded["sources"].values())
    assert roles.count("henan") == 5 and roles.count("national") == 2
    assert len(loaded["approved_corpus_unit_sha256"]) == 3


def test_henan_with_national_sources_is_served(tmp_path):
    units = [HENAN, NATIONAL]
    service = FlowService(units, corpus_sha(units), tmp_path / "tasks", region_profile=profile(units))
    task_id = service.open("首次申领测试证工本费是多少？")["task_id"]
    result = service.submit(task_id, answer("首次申领工本费23元。", ("首次申领测试证工本费23元", "HN-A", "正文段落1",
                                                                  "首次申领测试证工本费23元")))
    assert result["result_kind"] == "answer" and result["final_checks"]["passed"]


def test_m08_a_mixed_corpus_is_refused_at_startup_instead_of_answering_with_nanjing_text(tmp_path):
    mixed = [HENAN, NANJING]
    with pytest.raises(regional_scope.RegionScopeError, match="SOURCE_NOT_APPROVED: NJ-X"):
        FlowService(mixed, corpus_sha(mixed), tmp_path / "tasks", region_profile=profile([HENAN]))


def test_an_unapproved_corpus_hash_is_refused(tmp_path):
    units = [HENAN]
    with pytest.raises(regional_scope.RegionScopeError, match="CORPUS_NOT_APPROVED"):
        FlowService(units, "0" * 64, tmp_path / "tasks", region_profile=profile(units))


def test_a_displayed_answer_citing_another_region_is_detected():
    final = answer("x", ("南京99元", "NJ-X", "正文段落1", "99元"), ("河南23元", "HN-A", "正文段落1", "23元"))
    assert regional_scope.unapproved_citations(final, profile([HENAN])) == ["NJ-X"]

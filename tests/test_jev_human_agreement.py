import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jev_human_agreement as agreement  # noqa: E402


def test_view_and_comparison_keep_uncertain_apart():
    results = [{"question_id": "Q1", "method": "m1", "verdict": "supported"},
               {"question_id": "Q1", "method": "m2", "verdict": "supported"},
               {"question_id": "Q1", "method": "m2", "verdict": "unsupported"},
               {"question_id": "Q2", "method": "m1", "verdict": "uncertain"}]
    view = agreement.jev_view(results)
    assert view == {("Q1", "m1"): "是", ("Q1", "m2"): "否", ("Q2", "m1"): "不确定"}
    key = {"Q1": {"A": "m1", "B": "m2"}, "Q2": {"A": "m1", "B": "m2"}}
    sheet = {("Q1", "A"): {"citations_support": "是"}, ("Q1", "B"): {"citations_support": "是"},
             ("Q2", "A"): {"citations_support": "否"}, ("Q2", "B"): {"citations_support": "不适用"}}
    out = agreement.compare(view, key, {"甲": sheet})
    row = out["by_reviewer"]["甲"]
    assert row["matrix"] == {"人工是/Jev是": 1, "人工是/Jev否": 1, "人工否/Jev不确定": 1}
    assert row["agreement_when_jev_decided"] == "1/2" and row["disagreements"] == ["Q1:m2"]
    assert out["skipped_no_jev_claims"] == 0

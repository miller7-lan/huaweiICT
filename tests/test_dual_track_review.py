"""Protect pending ratings, sample membership and denominator accounting."""
import pytest

from dual_track_review import score


def data():
    item = dict(item_id="a", batch="F01", track="A3", review_kind="开发者人工审核", type="amount")
    return [{**{k: v for k, v in item.items() if k != "type"},
             "reviewer": "", "decision": "", "reviewed_at": "", "note": ""}], {"items": [item]}


def test_unrated_is_pending_and_has_no_precision():
    rows, manifest = data()
    result = score(rows, manifest)
    assert result["pending"] == 1 and result["completed"] == 0
    assert result["strict_correct_over_completed"] is None


def test_unknown_is_completed_but_not_judged():
    rows, manifest = data()
    rows[0].update(decision="无法判断", reviewer="开发者本人", reviewed_at="2026-11-15", note="测试夹具：缺原件")
    result = score(rows, manifest)
    assert result["completed"] == 1 and result["strict_correct_over_completed"] == 0
    assert result["strict_correct_over_judged"] is None


def test_duplicates_and_changed_batch_cannot_enter_scores():
    rows, manifest = data()
    with pytest.raises(ValueError, match="IDS"):
        score(rows * 2, manifest)
    rows[0]["batch"] = "F99"
    with pytest.raises(ValueError, match="METADATA"):
        score(rows, manifest)


def test_unattributed_or_unexplained_decisions_are_refused():
    rows, manifest = data()
    rows[0].update(decision="错误", reviewed_at="2026-11-15")
    with pytest.raises(ValueError, match="ACTUAL"):
        score(rows, manifest)
    rows[0]["reviewer"] = "开发者本人"
    with pytest.raises(ValueError, match="REASON"):
        score(rows, manifest)

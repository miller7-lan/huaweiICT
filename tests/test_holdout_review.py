"""Unblinding of returned holdout review sheets."""
import csv

import build_holdout_review_package as package
import score_holdout_review as review


def sheet(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(package.COLUMNS)
        writer.writerows(rows)
    return path


def row(qid, label, reviewer, acceptable="是", overall="2", error="否"):
    return [qid, "答复·单文档", "答复", "答复", label, "答复", acceptable, "全部", error, "是", "否", overall, reviewer, ""]


def test_unblinds_counts_agreement_and_reports_invalid_rows(tmp_path):
    key = {"Q1": {"A": "proposed", "B": "baseline"}, "Q2": {"A": "baseline", "B": "proposed"}}
    first = sheet(tmp_path / "one.csv", [row("Q1", "A", "甲"), row("Q1", "B", "甲", "否", "0", "是"),
                                         row("Q2", "A", "甲", overall="1"), row("Q2", "B", "甲", overall="")])
    second = sheet(tmp_path / "two.csv", [row("Q1", "A", "乙", overall="1")])
    result = review.score([first, second], key)
    assert result["by_method"]["proposed"]["ratings"] == 2  # Q1-A twice; Q2-B was invalid
    assert result["by_method"]["proposed"]["overall"] == {"2": 1, "1": 1, "0": 0}
    assert result["by_method"]["baseline"]["factual_errors"] == 1
    assert result["by_method"]["baseline"]["outcome_acceptable"] == 1
    assert result["agreement"] == {"double_rated_outputs": 1, "same_outcome_acceptable": 1, "same_overall": 0}
    assert result["invalid_rows"] == [{"sheet": "one.csv", "line": 5, "fields": ["总评(2/1/0)"]}]
    assert result["reviewers"] == ["乙", "甲"]

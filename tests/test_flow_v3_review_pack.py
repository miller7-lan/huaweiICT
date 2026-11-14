"""Blind review pack and scoring for the paired run (synthetic data)."""
import csv
import json

import pytest

import build_flow_v3_review_pack as pack
import score_flow_v3_review as score

CORPUS = {("D", "段1"): {"source_id": "D", "locator": "段1", "text": "换领工本费20元。"}}
ANSWER = {"outcome": "answer", "answer": "工本费20元。", "stop_reason": "", "unknowns": ["某缺项"],
          "assertions": [{"claim": "工本费20元", "source_id": "D", "locator": "段1", "quote": "工本费20元"}]}
STOP = {"outcome": "stop", "answer": "未作答", "assertions": [], "stop_reason": "证据不足", "unknowns": []}


def records(ids=("Q1", "Q2", "Q3")):
    return [json.dumps({"question_id": q, "compared_as": m, "question": "问" + q, "final": f})
            for q in ids for m, f in (("single_shot", STOP), ("flow_v3", ANSWER))]


def make(tmp_path, ids=("Q1", "Q2", "Q3")):
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(records(ids)), "utf-8")
    return pack.build(pack.read_records(path), CORPUS, tmp_path / "pack", "s", ["r1", "r2"])


def test_pack_hides_methods_and_shows_full_cited_text(tmp_path):
    key = make(tmp_path)
    text = (tmp_path / "pack/review.md").read_text("utf-8")
    assert "single_shot" not in text and "flow_v3" not in text and "换领工本费20元。" in text
    assert all(set(pair) == {"A", "B"} and set(pair.values()) == {"single_shot", "flow_v3"} for pair in key.values())
    assert json.loads((tmp_path / "pack/key.json").read_text("utf-8")) == key
    assert len({tuple(pair.values()) for pair in key.values()}) == 2  # the order varies by question
    rows = list(csv.DictReader((tmp_path / "pack/sheet-r1.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 6 and {r["reviewer"] for r in rows} == {"r1"}


def test_incomplete_pair_is_rejected(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(records(("Q1",))[0], "utf-8")
    with pytest.raises(ValueError, match="TWO_TO_SIX_METHODS_REQUIRED"):
        pack.read_records(path)
    path.write_text("\n".join(records(("Q1", "Q2"))[:3]), "utf-8")
    with pytest.raises(ValueError, match="INCOMPLETE_PAIR"):
        pack.read_records(path)


def fill(path, values):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for row in rows:
        row.update(values)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_scoring_unblinds_and_checks_values(tmp_path, monkeypatch):
    make(tmp_path)
    good = {"conclusions_correct": "是", "missing_points": "否", "citations_support": "是", "excerpt_relevant": "无摘录"}
    for name in ("r1", "r2"):
        fill(tmp_path / f"pack/sheet-{name}.csv", good)
    fill(tmp_path / "pack/sheet-r2.csv", {**good, "conclusions_correct": "部分"})
    key = json.loads((tmp_path / "pack/key.json").read_text("utf-8"))
    sheets = [score.read_sheet(tmp_path / f"pack/sheet-{n}.csv", key) for n in ("r1", "r2")]
    assert sheets[0][("Q1", "A")]["conclusions_correct"] == "是" and sheets[1][("Q1", "A")]["conclusions_correct"] == "部分"
    fill(tmp_path / "pack/sheet-r1.csv", {**good, "missing_points": "也许"})
    with pytest.raises(ValueError, match="INVALID_VALUE"):
        score.read_sheet(tmp_path / "pack/sheet-r1.csv", key)


def test_extra_judgements_are_read_from_the_fixed_remark_format_only():
    assert score.extras("停止判断=不合理；参考答案=待确认") == {"stop_judgment": "不合理", "reference_issue": "待确认"}
    assert score.extras("停止判断=看情况；这题我觉得参考写错了") == {}
    assert score.extras("") == {} and score.extras(None) == {}


def three_arm_records(ids=("Q1", "Q2")):
    return [json.dumps({"question_id": q, "compared_as": m, "question": "问" + q, "final": f})
            for q in ids for m, f in (("baseline", ANSWER), ("single_shot", STOP), ("flow_v3", ANSWER))]


def test_three_methods_get_three_labels_and_the_reference_is_shown(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(three_arm_records()), "utf-8")
    reference = {q: {"id": q, "expected_outcome": "answer", "category": "comparison", "key_points": ["要点甲"]}
                 for q in ("Q1", "Q2")}
    key = pack.build(pack.read_records(path), CORPUS, tmp_path / "pack", "s", ["r1", "r2"], reference)
    assert all(sorted(pair) == ["A", "B", "C"] and sorted(pair.values()) == ["baseline", "flow_v3", "single_shot"]
               for pair in key.values())
    text = (tmp_path / "pack/review.md").read_text("utf-8")
    assert "要点甲" in text and "期望答复" in text and "baseline" not in text and "输出 C" in text
    rows = list(csv.DictReader((tmp_path / "pack/sheet-r1.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 6 and "stop_judgment" in rows[0] and "reference_issue" in rows[0]


def test_extra_columns_are_validated_and_win_over_the_remark(tmp_path):
    make(tmp_path)
    key = json.loads((tmp_path / "pack/key.json").read_text("utf-8"))
    good = {"conclusions_correct": "是", "missing_points": "否", "citations_support": "是", "excerpt_relevant": "无摘录"}
    sheet = tmp_path / "pack/sheet-r1.csv"
    fill(sheet, {**good, "stop_judgment": "不适用", "reference_issue": "有误", "remark": "停止判断=合理；参考答案=未发现"})
    row = score.read_sheet(sheet, key)[("Q1", "A")]
    assert (row["stop_judgment"], row["reference_issue"]) == ("不适用", "有误")
    fill(sheet, {**good, "stop_judgment": "大概合理", "reference_issue": "未发现"})
    with pytest.raises(ValueError, match="INVALID_VALUE"):
        score.read_sheet(sheet, key)


def test_reference_is_validated_before_a_run_and_only_its_hash_is_kept(tmp_path):
    import compare_frozen_flow_v3 as compare
    questions = [{"id": "Q1", "question": "甲"}, {"id": "Q2", "question": "乙"}]
    good = [{"id": "Q1", "expected_outcome": "answer", "category": "comparison", "key_points": ["要点"]},
            {"id": "Q2", "expected_outcome": "stop", "category": "should_stop", "key_points": []}]
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(good, ensure_ascii=False), "utf-8")
    frozen = compare.check_reference(path, questions)
    assert frozen["expected"] == {"answer": 1, "stop": 1} and len(frozen["reference_sha256"]) == 64
    assert "要点" not in json.dumps(frozen, ensure_ascii=False)
    for broken in ([good[0]], [{**good[0], "key_points": []}, good[1]], [{**good[0], "category": "other"}, good[1]]):
        path.write_text(json.dumps(broken, ensure_ascii=False), "utf-8")
        with pytest.raises(ValueError):
            compare.check_reference(path, questions)
    assert compare.ARMS == {"baseline": 1, "single_shot": 1, "flow_v3": 3}


def test_verification_stops_look_the_same_for_every_method(tmp_path):
    single = {"outcome": "stop", "answer": "回答未通过引用与数字核验，已改为停止作答。", "assertions": [],
              "stop_reason": "VERIFICATION_FAILED", "unknowns": ["A1_QUANTITY_NOT_QUOTED:60元"]}
    flowed = {**single, "answer": "回答经修复后仍没有可通过引用与数字核验的结论，已停止作答。"}
    assert pack.render_output("A", single, CORPUS)[1:] == pack.render_output("A", flowed, CORPUS)[1:]
    assert "QUANTITY" not in "".join(pack.render_output("A", single, CORPUS))

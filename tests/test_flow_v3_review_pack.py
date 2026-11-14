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

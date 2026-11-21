import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_authored_round as intake  # noqa: E402

PLAN = ([("comparison", None)] * 8 + [("temporal", None)] * 4 + [("inference", None)] * 4
        + [("should_stop", reason) for reason in ("insufficient", "conflict", "branch", "out_of_scope") for _ in (0, 1)])


def write_round(tmp_path, change=None):
    questions, reference = [], []
    for n, (category, reason) in enumerate(PLAN, 1):
        item_id = f"X{n:02d}"
        questions.append({"id": item_id, "question": f"第{n}项办理时限和收费分别是多少？"})
        if reason:
            entry = {"id": item_id, "expected_outcome": "stop", "category": category, "key_points": [],
                     "stop_reason": reason, "stop_basis": "资料缺少所问的一项", "sources": ["资料1 · 第2条"]}
        else:
            sources = ["资料1 · 第3条"] if category == "inference" else ["资料1 · 第3条", "资料5 · 表格第4行"]
            entry = {"id": item_id, "expected_outcome": "answer", "category": category,
                     "key_points": ["工本费每证20元"], "sources": sources, "quotes": ["工本费每证20元"]}
        reference.append(entry)
    if change:
        change(questions, reference)
    paths = {name: tmp_path / f"{name}.json" for name in ("questions", "reference", "freeze")}
    paths["questions"].write_text(json.dumps(questions, ensure_ascii=False), "utf-8")
    paths["reference"].write_text(json.dumps(reference, ensure_ascii=False), "utf-8")
    paths["freeze"].write_text(json.dumps({"files_read": ["资料1"], "ai_tools_used": [], "declaration": "是"},
                                          ensure_ascii=False), "utf-8")
    return paths


def run(paths, **kwargs):
    return intake.check(paths["questions"], paths["reference"], paths["freeze"], **kwargs)


def test_well_formed_round_passes_and_prints_no_text(tmp_path):
    result = run(write_round(tmp_path))
    assert result["passed"], result["errors"]
    assert result["categories"] == intake.COUNTS and result["excel_questions"] == 12
    assert "工本费" not in json.dumps(result, ensure_ascii=False)


def test_giveaway_words_only_allowed_on_out_of_scope(tmp_path):
    def change(questions, reference):
        questions[16]["question"] = "这项规定现在还有效吗？"   # X17, insufficient
        questions[22]["question"] = "这项规定现在还有效吗？"   # X23, out_of_scope
    result = run(write_round(tmp_path, change))
    assert result["errors"] == ["GIVEAWAY_WORDING X17"]


def test_counts_materials_quotes_and_freeze_are_enforced(tmp_path):
    def change(questions, reference):
        reference[0]["sources"] = ["资料1 · 第3条"]
        reference[1]["quotes"] = []
        reference[16]["stop_reason"] = "conflict"
    paths = write_round(tmp_path, change)
    paths["freeze"].write_text("{}", "utf-8")
    errors = run(paths)["errors"]
    assert "NEEDS_TWO_MATERIALS X01" in errors and "QUOTE_PER_KEY_POINT_REQUIRED X02" in errors
    assert any(e.startswith("STOP_REASON_COUNTS") for e in errors)
    assert "FREEZE_FIELD_REQUIRED declaration" in errors


def test_quotes_checked_against_corpus_and_overlap_screened(tmp_path):
    paths = write_round(tmp_path)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"units": [{"text": "工本费 每证20元。"}]}, ensure_ascii=False), "utf-8")
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps([{"id": "P1", "question": "第1项办理时限和收费分别是多少？"}], ensure_ascii=False),
                     "utf-8")
    result = run(paths, corpus=corpus, prior=[prior])
    assert result["quotes"] == {"checked": 16, "not_found": 0}
    assert "X01" in result["overlap"]["above_0_5"] and result["passed"]


def test_table_converts_to_the_two_files(tmp_path):
    table = tmp_path / "table.csv"
    with open(table, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=intake.TABLE_COLUMNS)
        writer.writeheader()
        writer.writerow({"id": "X01", "category": "comparison", "expected_outcome": "answer", "stop_reason": "",
                         "question": "问题", "key_points": "要点一｜要点二", "sources": "资料1 · 第3条｜资料5 · 第4行",
                         "quotes": "原文一｜原文二", "stop_basis": "", "must_not": "把工作日写成自然日"})
    questions, reference = tmp_path / "q.json", tmp_path / "r.json"
    assert intake.from_table(table, questions, reference) == 1
    entry = json.loads(reference.read_text("utf-8"))[0]
    assert entry["key_points"] == ["要点一", "要点二"] and entry["must_not"] == ["把工作日写成自然日"]
    assert json.loads(questions.read_text("utf-8")) == [{"id": "X01", "question": "问题"}]


def test_excel_material_number_and_thin_coverage(tmp_path):
    result = run(write_round(tmp_path), excel_material=5)
    assert result["excel_questions"] == 12 and result["passed"]
    assert any(w.startswith("MATERIALS_IN_FEWER_THAN_TWO_QUESTIONS") for w in result["warnings"])
    assert not run(write_round(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path),
                   excel_material=3)["passed"]

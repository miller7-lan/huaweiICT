"""Audit items F16-F24: evaluation, scoring and native-run tools refuse what they cannot vouch for
(synthetic data, no model call, no network)."""
import csv
import json
import sys

import pytest

import build_flow_v3_review_pack as pack
import run_frozen_qa_native as native_run
import score_flow_v3_review as score
from gov_mcp.frozen_qa_flow_server import FlowService
from test_flow_v3_review_pack import CORPUS, records
from test_frozen_qa_flow_v3 import QUESTION, RENEW, UNITS, answer


def build_pack(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(records()), "utf-8")
    pack.build(pack.read_records(path), CORPUS, tmp_path / "pack", "s", ["r1", "r2"])
    rows = json.loads((tmp_path / "pack/rows.json").read_text("utf-8"))
    for name in ("r1", "r2"):
        sheet = tmp_path / f"pack/sheet-{name}.csv"
        data = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
        for row in data:
            stopped = rows[row["question_id"]][row["label"]]["outcome"] == "stop"
            row.update(conclusions_correct="不适用" if stopped else "是", missing_points="不适用" if stopped else "否",
                       citations_support="不适用" if stopped else "是", excerpt_relevant="无摘录",
                       stop_judgment="合理" if stopped else "不适用", reference_issue="未发现")
        with sheet.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=pack.COLUMNS)
            writer.writeheader()
            writer.writerows(data)
    return tmp_path / "pack"


def run_score(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["score", *map(str, args)])
    with pytest.raises(SystemExit) as stop:
        score.main()
    captured = capsys.readouterr()
    return stop.value.code, captured.out + captured.err


def test_f16_one_sheet_or_one_reviewer_never_counts_as_two(tmp_path, monkeypatch, capsys):
    folder = build_pack(tmp_path)
    one = folder / "sheet-r1.csv"
    code, text = run_score(monkeypatch, capsys, "--pack", folder, "--sheets", one, one,
                           "--output", tmp_path / "x.json")
    assert code == 2 and "DUPLICATE_SHEET_PATH" in text
    copy = tmp_path / "copy.csv"
    copy.write_text(one.read_text("utf-8-sig"), "utf-8-sig")  # same reviewer under another file name
    code, text = run_score(monkeypatch, capsys, "--pack", folder, "--sheets", one, copy,
                           "--output", tmp_path / "x.json")
    assert code == 2 and "SAME_REVIEWER_ON_TWO_SHEETS" in text
    mixed = list(csv.DictReader(one.open(encoding="utf-8-sig")))
    mixed[0]["reviewer"] = "r2"
    with copy.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.COLUMNS)
        writer.writeheader()
        writer.writerows(mixed)
    with pytest.raises(ValueError, match="SHEET_REVIEWER_NOT_UNIQUE"):
        score.sheet_reviewer(copy)
    with pytest.raises(ValueError, match="SHEET_REVIEWER_NOT_IN_PACK"):
        score.sheet_reviewer(one, ["r2", "r3"])


def test_f16_two_independent_reviewers_who_agree_everywhere_are_scored(tmp_path, monkeypatch, capsys):
    folder = build_pack(tmp_path)
    monkeypatch.setattr(score, "ROOT", tmp_path.parent)
    output = tmp_path.parent / "evidence" / (tmp_path.name + ".json")
    monkeypatch.setattr(sys, "argv", ["score", "--pack", str(folder), "--sheets", str(folder / "sheet-r1.csv"),
                                      str(folder / "sheet-r2.csv"), "--output", str(output)])
    score.main()
    result = json.loads(output.read_text("utf-8"))
    assert result["reviewers_distinct"] is True and result["legacy_pack_rules_not_checked"] is False
    assert set(result["reviewer_agreement"].values()) == {"6/6"}


def test_f19_missing_rules_never_exit_zero(tmp_path, monkeypatch, capsys):
    folder = build_pack(tmp_path)
    (folder / "rows.json").unlink()
    code, text = run_score(monkeypatch, capsys, "--pack", folder, "--sheets", folder / "sheet-r1.csv", "--check")
    assert code == 2 and "RULES_UNAVAILABLE" in text
    code, text = run_score(monkeypatch, capsys, "--pack", folder, "--sheets", folder / "sheet-r1.csv", "--check",
                           "--legacy-pack")
    assert code == 3 and '"rules_checked": false' in text


def test_f18_a_repeated_question_and_method_is_refused_with_its_lines(tmp_path):
    lines = records()
    extra = json.loads(lines[0])
    extra["final"] = {**extra["final"], "answer": "另一次运行"}
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(lines + [json.dumps(extra, ensure_ascii=False)]), "utf-8")
    with pytest.raises(ValueError, match="DUPLICATE_RECORD: Q1 single_shot on lines 1 and 7"):
        pack.read_records(path)
    other = json.loads(lines[1])
    other["question"] = "另一道题"
    path.write_text("\n".join([lines[0], json.dumps(other, ensure_ascii=False)] + lines[2:]), "utf-8")
    with pytest.raises(ValueError, match="QUESTION_DIFFERS_BETWEEN_METHODS: Q1"):
        pack.read_records(path)


def test_f23_the_pack_states_six_columns_one_rules_version_and_the_remark_return(tmp_path):
    folder = build_pack(tmp_path)
    text = (folder / "review.md").read_text("utf-8")
    assert "四列" not in text and "不上传" not in text and "六列" in text and pack.RULES_VERSION in text
    meta = json.loads((folder / "pack.json").read_text("utf-8"))
    assert meta["reviewers"] == ["r1", "r2"] and meta["rules_version"] == pack.RULES_VERSION
    assert all(column in text for column in list(pack.CHOICES) + list(pack.EXTRA_CHOICES))


def test_f20_an_empty_or_incomplete_native_stream_is_not_a_pass():
    empty = native_run.judge({"transport_completed": True, **native_run.summarize([])}, ("open", "submit"))
    assert empty["passed"] is False and "FINAL_ANSWER_MISSING_OR_NOT_UNIQUE" in empty["failures"]
    events = [{"type": "model_attempt_control", "phase": "begin"}, {"type": "tool", "tool_name": "submit"},
              {"type": "final_answer", "depth": 0, "content": "答复"}]
    wrong_order = native_run.judge({"transport_completed": True, **native_run.summarize(events)}, ("open", "submit"))
    assert wrong_order["protocol_valid"] is True and wrong_order["workflow_verified"] is False
    good = native_run.judge({"transport_completed": True, **native_run.summarize(
        events[:1] + [{"type": "tool", "tool_name": "open"}] + events[1:])}, ("open", "submit"))
    assert good["passed"] is True and good["failures"] == []
    cut = native_run.judge({"transport_completed": False, **native_run.summarize(events)}, ())
    assert cut["passed"] is False and cut["protocol_valid"] is False


def test_f24_the_public_receipt_holds_no_question_answer_or_usage_text():
    marker_q, marker_a = "合成题面标记QQ", "合成答复标记AA"
    events = [{"type": "model_attempt_control", "phase": "begin"},
              {"type": "token_count", "content": marker_a + " tokens"},
              {"type": "final_answer", "depth": 0, "content": marker_a}]
    report = native_run.judge({"question": marker_q, "transport_completed": True, **native_run.summarize(events)}, ())
    public = json.dumps(native_run.public_receipt(report), ensure_ascii=False)
    assert marker_q not in public and marker_a not in public and '"final_answer_chars": 8' in public


def test_f22_a_task_is_not_resumed_by_another_workflow_source_or_option(tmp_path):
    svc = FlowService(UNITS, "c" * 64, tmp_path / "tasks")
    task_id = svc.open(QUESTION)["task_id"]
    path = tmp_path / "tasks" / (task_id + ".json")
    task = json.loads(path.read_text("utf-8"))
    assert {"workflow", "source_sha256", "follow_up_after_answer"} <= set(task)
    for name, value in (("source_sha256", "0" * 64), ("workflow", "crossdoc-flow-v3"), ("follow_up_after_answer", True)):
        path.write_text(json.dumps({**task, name: value}), "utf-8")
        refused = svc.submit(task_id, answer("到期换领工本费20元。", RENEW))
        assert refused == {"status": "BLOCKED", "error": "TASK_BELONGS_TO_ANOTHER_WORKFLOW_SOURCE_OR_OPTIONS"}
    path.write_text(json.dumps(task), "utf-8")
    assert svc.submit(task_id, answer("到期换领工本费20元。", RENEW))["action"] == "final"


def test_f21_a_failed_model_call_is_still_an_attempt():
    from gov_mcp import frozen_qa_flow_v3 as flow

    def broken(messages):
        raise RuntimeError("合成传输错误")
    record = flow.ask(QUESTION, UNITS, broken)
    assert record["model"]["calls"] == [] and [a["status"] for a in record["model"]["attempts"]] == ["failed"]

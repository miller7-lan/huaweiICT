"""Input integrity checks for the isolated preparation parser."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("preparation_parser", Path(__file__).parents[1] / "scripts/prepare_topic3_data.py")
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)


def test_input_path_cannot_escape_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="inside workspace"):
        preparation.inside_workspace("../outside.txt")


def test_text_locations_preserve_original_lines_and_content_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    path = tmp_path / "source.txt"
    path.write_text("第一行\n\n第三行\n", encoding="utf-8")
    asset = {"path": "source.txt", "modality": "text"}
    parsed = preparation.parse_asset(asset, None)
    assert [row["locator"]["line"] for row in parsed["segments"]] == [1, 3]
    assert parsed["segments"][1]["content"] == "第三行"
    same = preparation.parse_asset(asset, None)
    assert parsed["asset_id"] == same["asset_id"]
    path.write_text("另一版本\n", encoding="utf-8")
    assert parsed["asset_id"] != preparation.parse_asset(asset, None)["asset_id"]


@pytest.mark.parametrize("content", ["a,a\n1,2\n", "a,b\n1,2,3\n", "a,b\n1\n"])
def test_corrupt_or_ambiguous_table_is_rejected(tmp_path, monkeypatch, content):
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    (tmp_path / "table.csv").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        preparation.parse_asset({"path": "table.csv", "modality": "table"}, None)


def test_quoted_multiline_csv_uses_record_locator(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    (tmp_path / "table.csv").write_text('材料,说明\n申请表,"第一行\n第二行"\n', encoding="utf-8", newline="")
    parsed = preparation.parse_asset({"path": "table.csv", "modality": "table"}, None)
    assert len(parsed["segments"]) == 1
    assert parsed["segments"][0]["locator"]["record_number"] == 1
    assert "第一行\n第二行" in parsed["segments"][0]["content"]


def test_office_file_is_not_silently_treated_as_text(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    (tmp_path / "source.docx").write_bytes(b"not-an-office-parser")
    with pytest.raises(ValueError, match="dedicated parser"):
        preparation.parse_asset({"path": "source.docx", "modality": "text"}, None)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import select_holdout_units as sel  # noqa: E402


def corpus():
    units = []
    for n in range(1, 40):
        units.append({"source_id": "HN-NORM-PUBLISHER", "locator": f"第{n}条", "text": "申请人应当提交材料。" * 12, "text_sha256": str(n)})
    units.append({"source_id": "HN-NORM-PUBLISHER", "locator": "第一百五十五条", "text": "条件" * 60, "text_sha256": "u1"})
    for source, loc in (("MOF-FIRST-ISSUE-2018", "正文段落{}"), ("HN-MEASURES-2024-JY", "正文段落{}"),
                        ("HN-FEE-CATALOG-XLSX-2025", "Excel工作表行政事业性收费!第{}行")):
        for n in range(1, 6):
            units.append({"source_id": source, "locator": loc.format(n), "text": "收费20元，" * 20, "text_sha256": source + str(n)})
    return units


def test_selection_is_reproducible_stratified_and_excludes_development_units():
    first, second = sel.select(corpus()), sel.select(corpus())
    assert first == second and [u["id"] for u in first] == ["H1", "H2", "H3", "H4", "H5", "H6"]
    picked = [(u["sources"][0]["source_id"], u["sources"][0]["locators"][0]["locator"]) for u in first]
    assert ("HN-NORM-PUBLISHER", "第一百五十五条") not in picked
    assert [s for s, _ in picked].count("HN-NORM-PUBLISHER") == 3
    assert all(u["kind"] in {"value", "material", "time_limit", "condition"} for u in first)
    assert sel.target_for("工本费40元")[0] == "value" and sel.target_for("无数字的条件")[0] == "condition"

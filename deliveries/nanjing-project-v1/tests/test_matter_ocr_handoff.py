"""Contract fixtures only: no OCR engine, human review or recognition accuracy claim."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.matter_ocr_handoff import consume, EXPECTED_SHA256

ROOT = Path(__file__).resolve().parents[1]
REQUEST = json.loads((ROOT / "data/real-matter-closure/nanjing/ocr-handoff/request-v1.json").read_text(encoding="utf-8"))
ORIGINAL = (ROOT / REQUEST["original_repository_path"]).read_bytes()


def response():
    return {"schema": 1, "engine": "windows.media.ocr", "language": "zh-Hans-CN",
        "input_sha256": EXPECTED_SHA256, "confidence_available": False, "requires_visual_review": True,
        "test_fixture": True, "pages": [{"page": i, "text": "模拟合同边界测试文字，不能当作真实识别文本。", "line_count": 2,
            "width": 1200, "height": 1800} for i in range(1, 5)]}


def test_all_pages_and_original_are_stored_without_review_or_publication(tmp_path):
    store = KnowledgeStore.initialize(tmp_path)
    result = consume(REQUEST, ORIGINAL, response(), store)
    assert result["pages"] == 4 and result["requires_visual_review"] and not result["confidence_available"]
    assert not result["human_business_review"] and not result["formal_publication"]
    assert result["review_version_head_counts"] == {"reviews": 0, "versions": 0, "heads": 0}
    with store.db() as db:
        row, chunks = store._content(db, result["revision_id"])
        assert row["sha256"] == EXPECTED_SHA256 and len(chunks) == 4
        assert all("OCR待核对" in c["locator"] for c in chunks)
        assert not json.loads(db.execute("SELECT config FROM sites").fetchone()[0])["enabled"]


@pytest.mark.parametrize("change", [
    lambda r: r.update(input_sha256="0" * 64),
    lambda r: r["pages"].pop(),
    lambda r: r["pages"].reverse(),
    lambda r: r.update(confidence_available=True),
    lambda r: r.update(requires_visual_review=False),
    lambda r: r["pages"][0].update(text="这里含有疑似号码123456789012345678，应停止保存"),
])
def test_forged_or_incomplete_response_stops_before_ingest(tmp_path, change):
    store = KnowledgeStore.initialize(tmp_path)
    data = response(); change(data)
    with pytest.raises(ValueError): consume(REQUEST, ORIGINAL, data, store)
    with store.db() as db: assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


def test_wrong_original_or_request_cannot_enter_downstream(tmp_path):
    store = KnowledgeStore.initialize(tmp_path)
    bad = deepcopy(REQUEST); bad["source_url"] = "https://unregistered.example/file.pdf"
    with pytest.raises(ValueError, match="已归档"): consume(bad, ORIGINAL, response(), store)
    with pytest.raises(ValueError, match="已归档"): consume(REQUEST, ORIGINAL + b"tampered", response(), store)

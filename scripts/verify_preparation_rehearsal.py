"""Verify the fixed synthetic fixture against independently stated expectations."""

import hashlib
import importlib.metadata
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import urllib.request

from PIL import Image
import rapidocr_onnxruntime

ROOT = Path(__file__).resolve().parents[1]


def main():
    parsed = json.loads((ROOT / ".runtime/preparation/run/assets.json").read_text(encoding="utf-8"))
    assert parsed["dataset_id"] == "ict-synthetic-preparation-v1" and parsed["data_class"] == "synthetic"
    assets = {asset["source_id"]: asset for asset in parsed["assets"]}
    assert set(assets) == {"demo-policy", "demo-materials", "demo-notice"}
    for asset in assets.values():
        source = ROOT / asset["path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == asset["sha256"]
    assert [segment["locator"]["line"] for segment in assets["demo-policy"]["segments"]] == [1, 2, 3]
    assert len(assets["demo-materials"]["segments"]) == 2
    image_text = "\n".join(segment["content"] for segment in assets["demo-notice"]["segments"])
    assert "申请表需要本人签字" in image_text
    assert "演练分支甲还需要演练证明" in image_text
    with Image.open(ROOT / assets["demo-notice"]["path"]) as image:
        for segment in assets["demo-notice"]["segments"]:
            assert 0 <= segment["confidence"] <= 1
            assert all(0 <= x <= image.width and 0 <= y <= image.height for x, y in segment["locator"]["polygon"])
    embedding = json.loads((ROOT / ".runtime/preparation/run/embedding.json").read_text(encoding="utf-8"))
    segments = [segment for asset in parsed["assets"] for segment in asset["segments"]]
    assert len(segments) == 9
    assert len(embedding["vectors"]) == 10 and embedding["dimension"] == 1024
    assert all(len(vector) == 1024 and all(math.isfinite(x) for x in vector) and any(vector) for vector in embedding["vectors"])
    assert {row["segment_id"] for row in embedding["ranked"]} == {row["segment_id"] for row in segments}
    assert all(embedding["ranked"][i]["score"] >= embedding["ranked"][i+1]["score"] for i in range(8))
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    model = next(row for row in models if row["name"] == "qwen3-embedding:0.6b")
    ocr_files = list((Path(rapidocr_onnxruntime.__file__).parent / "models").glob("*.onnx"))
    assert ocr_files
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
              "synthetic_fixture_verified": True, "asset_hashes_match": True,
              "ocr_expected_phrases_verified": True, "ocr_boxes_within_image": True,
              "segment_count": 9, "vector_count_including_query": 10, "dimension": 1024,
              "embedding_model_digest": model["digest"], "embedding_model_bytes": model["size"],
              "ocr_model_files": [{"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in ocr_files],
              "dependency_versions": {name: importlib.metadata.version(name) for name in ("rapidocr-onnxruntime", "Pillow", "onnxruntime", "numpy")},
              "business_accuracy_evaluated": False, "nexent_knowledge_ingestion_verified": False}
    path = ROOT / "evidence/toolchain/preparation-20261014/fixture-verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "summary": str(path), "segments": 9, "dimension": 1024}))


if __name__ == "__main__":
    main()

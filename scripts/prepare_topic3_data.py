"""Isolated, replaceable text/CSV/image OCR and embedding preparation rehearsal.

No Nexent publication, business database changes, ontology extraction or
business inference. Private outputs may contain source text; public summary
contains only hashes, counts, parser names and outcomes.
"""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inside_workspace(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Input path must stay inside workspace")
    return path


def generate_demo():
    from PIL import Image, ImageDraw, ImageFont
    directory = ROOT / ".runtime/preparation/demo"
    directory.mkdir(parents=True, exist_ok=True)
    policy = "模拟数据：仅用于工具演练，不是官方政策。\n演练事项由演练窗口受理。\n本次演练需要申请表，其他材料以模拟材料表为准。\n"
    materials = "材料名称,适用条件,要求\n申请表,所有模拟申请,必交\n演练证明,选择演练分支甲,条件交\n"
    for name, text in (("policy.txt", policy), ("materials.csv", materials)):
        path = directory / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    image_path = directory / "notice.png"
    if not image_path.exists():
        font_paths = [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]
        font_path = next((path for path in font_paths if path.exists()), None)
        if font_path is None:
            raise RuntimeError("A Chinese font is required to generate the image fixture")
        font = ImageFont.truetype(str(font_path), 34)
        image = Image.new("RGB", (1300, 340), "white")
        draw = ImageDraw.Draw(image)
        for index, text in enumerate(("模拟数据：仅用于工具演练", "演练事项材料提交说明", "申请表需要本人签字。", "演练分支甲还需要演练证明。")):
            draw.text((36, 24 + 72 * index), text, font=font, fill="black")
        image.save(image_path)
    return directory


def parse_asset(asset, engine):
    path = inside_workspace(asset["path"])
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Missing input or input exceeds 20 MiB rehearsal limit")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset_id = "asset-" + digest[:20]
    modality = asset["modality"]
    suffix = path.suffix.lower()
    segments = []

    def add(content, locator, confidence=None):
        if not content.strip():
            return
        if len(content) > 4096 or len(segments) >= 128:
            raise ValueError("Input needs chunking before this bounded rehearsal")
        segment_id = "segment-" + hashlib.sha256((asset_id + json.dumps(locator, sort_keys=True)).encode()).hexdigest()[:20]
        segments.append({"segment_id": segment_id, "asset_id": asset_id,
                         "content": content, "locator": locator, "confidence": confidence,
                         "review_status": "pending_review"})

    if modality == "text" and suffix in (".txt", ".md"):
        parser = "utf8-lines-v1"
        for line_number, content in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            add(content, {"kind": "line", "line": line_number})
    elif modality == "table" and suffix == ".csv":
        parser = "csv-records-v1"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames
            if not columns or len(set(columns)) != len(columns):
                raise ValueError("CSV needs unique named columns")
            for record_number, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV column count mismatch")
                add("；".join(f"{column}：{row[column]}" for column in columns),
                    {"kind": "csv_record", "record_number": record_number, "columns": columns})
    elif modality == "image" and suffix in (".png", ".jpg", ".jpeg"):
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        if engine is None:
            raise RuntimeError("OCR engine unavailable")
        parser = "rapidocr-onnxruntime-" + importlib.metadata.version("rapidocr-onnxruntime")
        rows, _elapsed = engine(str(path))
        if not rows:
            raise ValueError("OCR produced no readable text")
        for box, content, confidence in rows:
            add(content, {"kind": "image_polygon", "page": 1, "unit": "pixel",
                          "polygon": [[float(x), float(y)] for x, y in box]}, float(confidence))
    else:
        raise ValueError("Unsupported modality/extension: add a dedicated parser")
    if not segments:
        raise ValueError("No usable segments")
    return {**asset, "asset_id": asset_id, "sha256": digest, "bytes": path.stat().st_size,
            "parser": parser, "parsed": True, "segments": segments}


def cosine(left, right):
    denominator = math.sqrt(sum(x*x for x in left) * sum(y*y for y in right))
    if denominator == 0:
        raise ValueError("Zero norm vector")
    return sum(x*y for x, y in zip(left, right)) / denominator


def embed(profile, segments):
    if profile.get("embedding_provider") == "unconfigured_cloud":
        raise ValueError("Cloud embedding provider is not configured; DeepSeek chat is not an embedding API")
    # Offline provider at the loopback address; no credentials or external API.
    base = profile["embedding_host_url"]
    if base != "http://127.0.0.1:11434":
        raise ValueError("This rehearsal only supports the configured local Ollama")
    inputs = [profile["query_instruction"] + profile["query"]] + [row["content"] for row in segments]
    request = urllib.request.Request(base + "/api/embed", method="POST",
        headers={"Content-Type": "application/json"}, data=json.dumps({
            "model": profile["embedding_model"], "input": inputs, "truncate": False,
            "keep_alive": 0}).encode())
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    vectors = result.get("embeddings", [])
    if len(vectors) != len(inputs) or not vectors:
        raise ValueError("Embedding response count mismatch")
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension or not all(math.isfinite(x) for x in vector) for vector in vectors):
        raise ValueError("Embedding dimensions or values are invalid")
    ranked = sorted(({"segment_id": segment["segment_id"], "asset_id": segment["asset_id"],
                      "locator": segment["locator"], "score": cosine(vectors[0], vector)}
                     for segment, vector in zip(segments, vectors[1:])), key=lambda row: row["score"], reverse=True)
    return {"model": profile["embedding_model"], "dimension": dimension,
            "vector_count": len(vectors), "ranked": ranked, "vectors": vectors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-demo", action="store_true")
    parser.add_argument("--manifest", default="config/preparation/assets.demo.json")
    parser.add_argument("--profile", default="config/preparation/profile.json")
    parser.add_argument("--embed", action="store_true")
    parser.add_argument("--classify", action="store_true", help="Optional Jev candidates; failures do not block embedding")
    parser.add_argument("--classification-provider", choices=["jev", "vercel_jev_live"], help="Explicit classification transport; does not enable any chat model")
    parser.add_argument("--classification-config", default="config/preparation/classification.json")
    args = parser.parse_args()
    profile = json.loads(inside_workspace(args.profile).read_text(encoding="utf-8-sig"))
    manifest = json.loads(inside_workspace(args.manifest).read_text(encoding="utf-8-sig"))
    output = inside_workspace(profile["output_directory"])
    if not output.is_relative_to(ROOT / ".runtime/preparation"):
        raise ValueError("Raw outputs must stay in private preparation directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    public = ROOT / "evidence/toolchain" / ("preparation-" + stamp) / "summary.json"
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
              "scope": "isolated input preparation, not Nexent knowledge ingestion or business acceptance",
              "dataset_id": manifest.get("dataset_id"), "data_class": manifest.get("data_class"),
              "assets": [], "errors": [], "embedding_requested": args.embed,
              "business_rules_published": False, "ontology_extraction_implemented": False}
    assets = []
    stage = "manifest"
    try:
        if not manifest.get("assets") or manifest.get("data_class") not in ("synthetic", "public", "private"):
            raise ValueError("Manifest needs assets and an explicit data class")
        if args.generate_demo:
            stage = "generate_demo"
            generate_demo()
        stage = "ocr_initialization"
        engine = None
        if any(asset["modality"] == "image" for asset in manifest["assets"]):
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR(det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                              det_intra_op_num_threads=1, cls_intra_op_num_threads=1, rec_intra_op_num_threads=1)
        seen_sources = set()
        for asset in manifest["assets"]:
            stage = "parse/" + asset["source_id"]
            if asset["source_id"] in seen_sources:
                raise ValueError("Duplicate source id")
            seen_sources.add(asset["source_id"])
            parsed = parse_asset(asset, engine)
            assets.append(parsed)
            report["assets"].append({key: parsed[key] for key in ("source_id", "asset_id", "sha256", "bytes", "modality", "parser", "parsed")})
            report["assets"][-1]["segment_count"] = len(parsed["segments"])
        stage = "save_private_segments"
        write_json(output / "assets.json", {"dataset_id": manifest["dataset_id"], "data_class": manifest["data_class"], "assets": assets})
        if args.classify:
            stage = "classification"
            try:
                from classify_topic3_data import classify_prepared
                run, path = classify_prepared(
                    {"dataset_id": manifest["dataset_id"], "data_class": manifest["data_class"], "assets": assets},
                    inside_workspace(args.classification_config), ROOT / ".runtime/preparation/classification",
                    provider_name=args.classification_provider)
                report["classification"] = {"output": str(path.relative_to(ROOT)), **run["summary"]}
            except Exception:
                # Optional classification/config/output failure must not stop local retrieval.
                # The saved parsed corpus remains available for a corrected rerun.
                report["classification"] = {"status": "error", "message_code": "CLASSIFICATION_STAGE_FAILED",
                    "all_segments_pending_review": True, "business_rules_published": False}
        if args.embed:
            stage = "embedding"
            embedding = embed(profile, [segment for asset in assets for segment in asset["segments"]])
            write_json(output / "embedding.json", embedding)
            report["embedding"] = {key: embedding[key] for key in ("model", "dimension", "vector_count")}
            report["embedding"]["ranked_segment_count"] = len(embedding["ranked"])
        report["input_preparation_passed"] = True
    except Exception as error:
        report["input_preparation_passed"] = False
        report["errors"].append({"stage": stage, "type": type(error).__name__})
    write_json(public, report)
    print(json.dumps({"summary": str(public), "passed": report["input_preparation_passed"],
                      "assets": len(report["assets"]), "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["input_preparation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

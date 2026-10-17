"""Download pinned public Qwen training weights from its official ModelScope repo.

No credentials or project data are sent. Every downloaded file must match its
committed size and SHA-256 before it becomes available to the training runner.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "config/training/qwen3-4b.modelscope.json"


def file_sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b""):
            value.update(block)
    return value.hexdigest()


def validate_spec(spec):
    if spec.get("repository") != "Qwen/Qwen3-4B":
        raise ValueError("Only the approved public Qwen3-4B repository is supported")
    names = set()
    for row in spec["files"]:
        name = row["Path"]
        if name in names or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
            raise ValueError("Invalid or duplicate model filename")
        names.add(name)
        if not re.fullmatch("[a-f0-9]{40}", row["Revision"]) or not re.fullmatch("[a-f0-9]{64}", row["Sha256"]):
            raise ValueError("Invalid pinned revision or digest")
        if type(row["Size"]) is not int or not 0 < row["Size"] <= 5 * 1024**3:
            raise ValueError("Invalid model file size")
    required = {"config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors.index.json"}
    if not required <= names or len([n for n in names if n.endswith(".safetensors")]) != 3:
        raise ValueError("Incomplete pinned Qwen3-4B file list")


def verify_base(directory: Path, spec: dict):
    validate_spec(spec)
    for row in spec["files"]:
        path = directory / row["Path"]
        if not path.is_file() or path.stat().st_size != row["Size"] or file_sha(path) != row["Sha256"]:
            raise ValueError("Base file missing or integrity mismatch: " + row["Path"])
    cfg = json.loads((directory / "config.json").read_bytes())
    if cfg.get("model_type") != "qwen3" or cfg.get("architectures") != ["Qwen3ForCausalLM"]:
        raise ValueError("Unexpected base model architecture")


def download_file(directory, row, direct):
    target = directory / row["Path"]
    if target.exists():
        if target.stat().st_size == row["Size"] and file_sha(target) == row["Sha256"]:
            print(json.dumps({"file": row["Path"], "status": "verified_cached"}), flush=True)
            return
        raise ValueError("Existing final model file has wrong digest; refusing to overwrite it")
    partial = target.with_name(target.name + ".part")
    position = partial.stat().st_size if partial.exists() else 0
    url = ("https://modelscope.cn/models/Qwen/Qwen3-4B/resolve/" + row["Revision"] + "/" + row["Path"])
    headers = {"User-Agent": "govkb-local-qwen-training/1.0"}
    if position:
        headers["Range"] = f"bytes={position}-"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    if position < row["Size"]:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            mode = "ab" if position and response.status == 206 else "wb"
            if mode == "ab" and not response.headers.get("Content-Range", "").startswith(f"bytes {position}-"):
                raise ValueError("Invalid resumed content range")
            if mode == "wb":
                position = 0
            printed = position
            with partial.open(mode) as stream:
                for block in iter(lambda: response.read(4 * 1024**2), b""):
                    position += len(block)
                    if position > row["Size"]:
                        raise ValueError("Downloaded content exceeds pinned size")
                    stream.write(block)
                    if position - printed >= 256 * 1024**2:
                        print(json.dumps({"file": row["Path"], "downloaded_mib": position // 1024**2,
                                          "total_mib": row["Size"] // 1024**2}), flush=True)
                        printed = position
    if partial.stat().st_size != row["Size"] or file_sha(partial) != row["Sha256"]:
        raise ValueError("Downloaded model integrity mismatch: " + row["Path"])
    partial.replace(target)
    print(json.dumps({"file": row["Path"], "status": "sha256_verified"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/training/base/Qwen3-4B-modelscope")
    parser.add_argument("--direct-network", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_bytes())
    validate_spec(spec)
    args.output.mkdir(parents=True, exist_ok=True)
    # Validate small configuration/tokenizer files before starting large weights.
    small = [f for f in spec["files"] if not f["Path"].endswith(".safetensors")]
    large = [f for f in spec["files"] if f["Path"].endswith(".safetensors")]
    for row in small:
        download_file(args.output, row, args.direct_network)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda row: download_file(args.output, row, args.direct_network), large))
    verify_base(args.output, spec)
    (args.output / "verified-source.json").write_bytes(args.spec.read_bytes())
    print(json.dumps({"status": "complete", "repository": spec["repository"],
                      "source_spec_sha256": file_sha(args.spec)}), flush=True)


if __name__ == "__main__":
    main()

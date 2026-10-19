"""Prepare immutable synthetic-only retrieval snapshot and private MCP config."""

import json
import secrets
import shutil
from pathlib import Path

from gov_mcp.asset_query import AssetQuery, digest
from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    runtime = RUNTIME / "asset-query"
    ensure_private_directory(runtime)
    source = json.loads((RUNTIME / "preparation/run/assets.json").read_text(encoding="utf-8"))
    embedding = json.loads((RUNTIME / "preparation/run/embedding.json").read_text(encoding="utf-8"))
    profile = json.loads((ROOT / "config/preparation/profile.json").read_text(encoding="utf-8"))
    if source.get("data_class") != "synthetic":
        raise ValueError("Only synthetic input is enabled in this rehearsal")
    segments = [row for asset in source["assets"] for row in asset["segments"]]
    if {row["segment_id"] for row in embedding["ranked"]} != {row["segment_id"] for row in segments}:
        raise ValueError("Rebuild vectors after changing sources")
    if len(embedding["vectors"]) != len(segments) + 1:
        raise ValueError("Corpus vector count mismatch")
    vectors = {"model": embedding["model"], "dimension": embedding["dimension"], "query_instruction": profile["query_instruction"],
               "rows": [{"segment_id": segment["segment_id"], "vector": vector} for segment, vector in zip(segments, embedding["vectors"][1:])]}
    original_paths = []
    for asset in source["assets"]:
        path = (ROOT / asset["path"]).resolve()
        if not path.is_relative_to(RUNTIME / "preparation"):
            raise ValueError("Rehearsal originals must stay in private preparation directory")
        filename = asset["asset_id"] + path.suffix.lower()
        original_paths.append((path, filename))
        asset["snapshot_file"] = "raw/" + filename
        asset.pop("path")
    fingerprint = digest({"assets": source, "vectors": vectors})
    index = {"knowledge_version": "docs-" + fingerprint[:16], "snapshot_sha256": fingerprint}
    directory = runtime / "snapshot"
    if directory.exists():
        old = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        if old != index:
            raise ValueError("Existing source snapshot is immutable; use a new isolated runtime for a changed corpus")
    else:
        (directory / "raw").mkdir(parents=True)
        for path, filename in original_paths:
            shutil.copyfile(path, directory / "raw" / filename)
        for name, value in (("assets.json", source), ("vectors.json", vectors), ("index.json", index)):
            (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    token_path = runtime / "mcp-token.txt"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(40), encoding="ascii")
    shutil.copyfile(RUNTIME / "mcp/role-map.json", runtime / "role-map.json")
    AssetQuery(runtime).load()
    print(json.dumps({"prepared": True, "assets": len(source["assets"]), "segments": len(segments), **index}))


if __name__ == "__main__":
    main()

"""Attach local Markdown source files to the already embedded frozen chunks.

Public source URLs are provenance, not Nexent storage object names. Using them
as path_or_url makes the UI's file-access check reject chunk previews. Upload
the seven files to this knowledge base's native storage, then update paths only.
No text/vector regeneration, permission changes or model calls are performed.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlencode

from nexent_api import NexentAPI
from provision_crossdoc_native_preview import read, write
from provision_frozen_knowledge import CORPUS, ROOT, find

LEDGER = ROOT / ".runtime/frozen-qa-20261030/kb/file-bindings.json"


def content_signature(rows):
    return {row["id"]: (row.get("content"), row.get("title"), row.get("filename")) for row in rows}


def bind(api):
    kb = find(api)
    if not kb:
        raise ValueError("FROZEN_KNOWLEDGE_BASE_REQUIRED")
    index = kb["name"]
    route = f"/indices/{index}/chunks"
    before = api.call("POST", route)["chunks"]
    corpus = read(CORPUS)
    counts = Counter(unit["code"] for unit in corpus["units"])
    expected = {f"{code}-{i:03d}" for code, count in counts.items() for i in range(1, count + 1)}
    if len(before) != len(expected) or set(content_signature(before)) != expected:
        raise ValueError("EXACT_FROZEN_CHUNK_SET_REQUIRED")
    saved = read(LEDGER) if LEDGER.exists() else {"index_name": index, "files": {}}
    if saved["index_name"] != index:
        raise ValueError("FILE_BINDINGS_BELONG_TO_ANOTHER_INDEX")
    export = ROOT / "artifacts/knowledge-20261105"
    manifest = read(export / "knowledge-manifest.json")
    by_name = {entry["file"]: entry for entry in manifest["files"]}
    sources = {source["code"]: source for source in corpus["manifest"]["sources"]}
    for code, source in sources.items():
        name = f"{code}-{source['source_id']}.md"
        raw = (export / "knowledge" / name).read_bytes()
        if sha256(raw).hexdigest() != by_name[name]["sha256"]:
            raise ValueError("EXPORTED_SOURCE_HASH_MISMATCH")
        if code not in saved["files"]:
            response = api.http.post(api.base + "/file/upload",
                                    headers={"Authorization": "Bearer " + api.token},
                                    data={"destination": "minio", "folder": "knowledge_base", "index_name": index},
                                    files=[("file", (name, raw, "text/markdown"))])
            if response.status_code != 200:
                raise ValueError("NATIVE_SOURCE_UPLOAD_FAILED_HTTP_" + str(response.status_code))
            data = response.json()
            paths = data.get("uploaded_file_paths", [])
            if len(paths) != 1 or not paths[0].startswith("knowledge_base/"):
                raise ValueError("ONE_NATIVE_KNOWLEDGE_OBJECT_REQUIRED")
            saved["files"][code] = {"path": paths[0], "sha256": sha256(raw).hexdigest()}
            write(LEDGER, saved)
        binding = saved["files"][code]
        if binding["sha256"] != sha256(raw).hexdigest():
            raise ValueError("BOUND_SOURCE_VERSION_CHANGED")
        current_paths = {row["id"]: row.get("path_or_url") for row in before}
        pending = [f"{code}-{i:03d}" for i in range(1, counts[code] + 1)
                   if current_paths.get(f"{code}-{i:03d}") != binding["path"]]
        def update(identifier):
            api.call("PUT", f"/indices/{index}/chunk/{identifier}",
                     {"path_or_url": binding["path"], "metadata": {"original_source_url": source["url"]}})
        # Disjoint existing chunks: bound local writes while ES waits for refresh.
        # On restart the readback above skips already bound chunks.
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(update, pending))
        print(f"{code}: storage path bound for {counts[code]} unchanged chunks", flush=True)
    after = api.call("POST", route)["chunks"]
    if content_signature(after) != content_signature(before):
        raise ValueError("FROZEN_CHUNK_CONTENT_CHANGED")
    checks = {}
    for code, binding in saved["files"].items():
        result = api.call("POST", route + "?" + urlencode({"page": 1, "page_size": 1, "path_or_url": binding["path"]}))
        if result.get("status") != "success" or result.get("total") != counts[code] or len(result["chunks"]) != 1:
            raise ValueError("NATIVE_DOCUMENT_PREVIEW_FAILED")
        checks[code] = result["total"]
    unauth = api.http.post(api.base + route).status_code
    if unauth not in (401, 403):
        raise ValueError("UNAUTHENTICATED_PREVIEW_NOT_REJECTED")
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "files_bound": len(checks),
            "per_source_preview_counts": checks, "chunks_unchanged": len(after),
            "content_titles_and_filenames_unchanged": True, "unauthenticated_status": unauth,
            "source_urls_preserved_as_metadata": True, "model_calls": 0, "permissions_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("7 native source uploads and 581 path-only updates; apply requires --apply")
        return
    api = NexentAPI()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        result = bind(api)
    finally:
        api.close()
    write(ROOT / "evidence/frozen-qa-20261030/kb-file-binding-20261105.json", result)
    import json
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

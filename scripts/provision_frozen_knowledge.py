"""Write the frozen Henan corpus into a Nexent knowledge base, one chunk per unit.

This Nexent deployment has no data-process service, so file upload/processing
fails (an earlier attempt left a separate base with seven failed file records).
Instead every corpus unit becomes a manual chunk: exact text as content, locator
as title, the source file as filename, provenance in metadata. Nexent embeds each
chunk with the base's local Ollama model, so no cloud model is called. Chunk ids
are deterministic, so rerunning overwrites rather than duplicates.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nexent_api import NexentAPI  # noqa: E402

CORPUS = ROOT / ".runtime/frozen-qa-20261030/corpus.json"
OUTPUT = ROOT / "evidence/frozen-qa-20261030/nexent-knowledge-readback.json"
KNOWLEDGE_NAME = "河南冻结资料逐单元-20261105"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
PROBES = ("到期换领居民身份证要交验什么证件", "首次申领居民身份证工本费停征", "河南身份证办理时限 工作日")


def read(path):
    return json.loads(Path(path).read_text("utf-8-sig"))


def find(api):
    # Only the stats form returns per-base details; display_name is the user-facing name.
    listed = api.call("GET", "/indices?include_stats=true")
    matches = [row for row in listed.get("indices_info", []) if row.get("display_name") == KNOWLEDGE_NAME]
    if len(matches) > 1:
        raise RuntimeError("AMBIGUOUS_KNOWLEDGE_BASE")
    return matches[0] if matches else None


def chunk(unit, index, url):
    return {"chunk_id": f"{unit['code']}-{index:03d}", "content": unit["text"], "title": unit["locator"],
            "filename": f"{unit['code']}-{unit['source_id']}.md", "path_or_url": url,
            "metadata": {"source_id": unit["source_id"], "locator": unit["locator"], "code": unit["code"],
                         "text_sha256": unit["text_sha256"], "evidence_kind": unit["evidence_kind"]}}


def provision(api):
    platform = read(ROOT / ".runtime/nexent-state.json")
    models = [m for m in api.call("GET", "/model/list")["data"]
              if m.get("model_type") == "embedding" and m.get("model_name") == EMBEDDING_MODEL
              and m.get("connect_status") == "available"]
    if len(models) != 1:
        raise RuntimeError("ONE_AVAILABLE_LOCAL_EMBEDDING_MODEL_REQUIRED")
    existing = find(api)
    if existing is None:
        api.call("POST", "/indices/" + KNOWLEDGE_NAME, {"embedding_model_id": models[0]["model_id"],
                 "group_ids": [platform["group_id"]], "ingroup_permission": "READ_ONLY"})
        existing = find(api)
        if existing is None:
            raise RuntimeError("KNOWLEDGE_BASE_NOT_LISTED_AFTER_CREATE")
    if existing.get("embedding_model_id") != models[0]["model_id"]:
        raise RuntimeError("KNOWLEDGE_BASE_EMBEDDING_MODEL_CHANGED")
    index_name = existing["name"]
    corpus = read(CORPUS)
    urls = {source["source_id"]: source["url"] for source in corpus["manifest"]["sources"]}
    counters = {}
    for unit in corpus["units"]:
        counters[unit["code"]] = counters.get(unit["code"], 0) + 1
        api.call("POST", f"/indices/{index_name}/chunk", chunk(unit, counters[unit["code"]], urls[unit["source_id"]]))
    stats = (find(api) or {}).get("stats", {}).get("base_info", {})
    probes = []
    for query in PROBES:
        result = api.call("POST", "/indices/search/hybrid", {"query": query, "index_names": [index_name], "top_k": 5})
        hits = result.get("results", result) if isinstance(result, dict) else result
        probes.append({"query": query, "hits": [
            {"title": hit.get("title"), "filename": hit.get("filename"), "score": hit.get("score")}
            for hit in (hits if isinstance(hits, list) else [])[:5]]})
    return {"knowledge_name": KNOWLEDGE_NAME, "index_name": index_name, "embedding_model": EMBEDDING_MODEL,
            "embedding_model_id": models[0]["model_id"], "embedding_dim": stats.get("embedding_dim"),
            "chunks_written": len(corpus["units"]), "chunk_count_reported": stats.get("chunk_count"),
            "corpus_sha256": corpus["manifest"]["corpus_sha256"], "per_source": counters,
            "probes": probes, "cloud_model_calls": 0, "embedding": "local_ollama"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create or reuse the knowledge base and write chunks")
    args = parser.parse_args()
    if not args.apply:
        units = read(CORPUS)["units"]
        print(json.dumps({"knowledge_name": KNOWLEDGE_NAME, "embedding_model": EMBEDDING_MODEL,
                          "chunks": len(units), "apply_requires": "--apply"}, ensure_ascii=False))
        return
    api = NexentAPI()
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        result = provision(api)
    finally:
        api.close()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

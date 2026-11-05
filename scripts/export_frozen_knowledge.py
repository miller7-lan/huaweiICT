"""Export the frozen Henan corpus as the knowledge-base files submitted with the Nexent configuration.

Each per-source Markdown keeps the source URL, original SHA-256, revision and one
heading per locator, so retrieval results trace back to exact units. A chunk
manifest lists the 581 chunk ids, titles, file names and text hashes that
scripts/provision_frozen_knowledge.py writes into Nexent (this deployment has no
file-parsing service, so chunks are written directly rather than uploaded).
Output is local only: government source text is not committed.
"""
import argparse
import json
from hashlib import sha256
from pathlib import Path
import zipfile

from provision_frozen_knowledge import CORPUS, EMBEDDING_MODEL, KNOWLEDGE_NAME, chunk

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/knowledge-20261105"


def render(source, units):
    lines = [f"# {source['code']} {source['source_id']}", "",
             f"- 来源：{source['url']}", f"- 原件 SHA-256：{source['original_sha256']}",
             f"- 修订：{source['revision']}",
             "- 说明：冻结的历史归档资料，未核定现行效力；引用时须保留下列定位。", ""]
    for unit in units:
        lines += [f"## {unit['locator']}", "", unit["text"], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    corpus = json.loads(CORPUS.read_text("utf-8"))
    folder = args.out / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    files, chunks, counters = [], [], {}
    urls = {source["source_id"]: source["url"] for source in corpus["manifest"]["sources"]}
    for source in corpus["manifest"]["sources"]:
        units = [unit for unit in corpus["units"] if unit["source_id"] == source["source_id"]]
        text = render(source, units)
        name = f"{source['code']}-{source['source_id']}.md"
        (folder / name).write_text(text, "utf-8", newline="\n")
        files.append({"file": name, "units": len(units), "sha256": sha256(text.encode()).hexdigest()})
    for unit in corpus["units"]:
        counters[unit["code"]] = counters.get(unit["code"], 0) + 1
        item = chunk(unit, counters[unit["code"]], urls[unit["source_id"]])
        chunks.append({key: item[key] for key in ("chunk_id", "title", "filename")} | item["metadata"])
    manifest = {"knowledge_name": KNOWLEDGE_NAME, "embedding_model": EMBEDDING_MODEL,
                "corpus_sha256": corpus["manifest"]["corpus_sha256"], "files": files,
                "chunk_count": len(chunks), "chunks": chunks,
                "rebuild": "scripts/provision_frozen_knowledge.py --apply (manual chunks; one per unit)"}
    (args.out / "knowledge-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                                       "utf-8", newline="\n")
    archive = args.out / "frozen-knowledge-20261105.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(args.out / "knowledge-manifest.json", "knowledge-manifest.json")
        for item in files:
            bundle.write(folder / item["file"], "knowledge/" + item["file"])
    print(json.dumps({"files": [(f["file"], f["units"]) for f in files], "chunks": len(chunks),
                      "archive": str(archive), "archive_sha256": sha256(archive.read_bytes()).hexdigest()},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

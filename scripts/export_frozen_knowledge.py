"""Export the frozen Henan corpus as per-source Markdown for a Nexent knowledge base.

Each file keeps the source URL, original SHA-256, revision and one heading per
locator, so Nexent retrieval results can be traced back to exact units. Files
go to the private runtime and are uploaded through the Nexent UI. Automated
upload is intentionally not implemented: creating the index needs an embedding
model configured in Nexent, which is a separate paid-API decision.
"""
import json
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / ".runtime/frozen-qa-20261030/corpus.json"
OUTPUT = ROOT / ".runtime/frozen-qa-20261030/nexent-knowledge"
INDEX = "govkb_henan_frozen_20261030"
# Nexent v2.7.0 config-service routes (vendor/nexent/backend/apps), for a later automated step.
ROUTES = {"create_index": "POST /indices/{index_name} (body: embedding_model_id)",
          "upload_files": "POST /file/upload", "process_files": "POST /file/process",
          "search": "POST /indices/search/hybrid (northbound)"}


def render(source, units):
    lines = [f"# {source['code']} {source['source_id']}", "",
             f"- 来源：{source['url']}", f"- 原件 SHA-256：{source['original_sha256']}",
             f"- 修订：{source['revision']}",
             "- 说明：冻结的历史归档资料，未核定现行效力；引用时须保留下列定位。", ""]
    for unit in units:
        lines += [f"## {unit['locator']}", "", unit["text"], ""]
    return "\n".join(lines)


def main():
    corpus = json.loads(CORPUS.read_text("utf-8"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    files = []
    for source in corpus["manifest"]["sources"]:
        units = [unit for unit in corpus["units"] if unit["source_id"] == source["source_id"]]
        text = render(source, units)
        name = f"{source['code']}-{source['source_id']}.md"
        (OUTPUT / name).write_text(text, "utf-8", newline="\n")
        files.append({"file": name, "units": len(units), "sha256": sha256(text.encode()).hexdigest()})
    plan = {"index_name": INDEX, "corpus_sha256": corpus["manifest"]["corpus_sha256"], "files": files,
            "nexent_routes": ROUTES, "automated_upload": "not_implemented",
            "next_steps": ["在 Nexent 模型管理中确认嵌入模型（会产生费用，需单独决定）",
                           f"在知识库页面新建 {INDEX} 并上传 {OUTPUT.relative_to(ROOT)} 下的文件",
                           "把该知识库绑定到检索子 Agent，推理与核验仍走 MCP 工具",
                           "用 run_frozen_qa.py 的同一批问题对比知识库检索与字面检索的命中"]}
    (OUTPUT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({"index_name": INDEX, "files": [(f["file"], f["units"]) for f in files]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

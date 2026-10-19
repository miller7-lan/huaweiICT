"""Build an attributed, reviewable real-source rule package without publication."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.knowledge_fetch import parse_html

BASE = ROOT / "data/real-matter-closure/nanjing"
CAPTURES = ROOT / ".runtime/real-matter-closure"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("已有交付文件内容不同，请建立新版本，不覆盖已核查包：" + path.name)
    path.write_bytes(raw)


def verified_capture(captures, group, source_id):
    directory = captures / group
    snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    matches = [s for s in snapshot["sources"] if s["source_id"] == source_id]
    if len(matches) != 1 or matches[0]["status"] != "captured_pending_review":
        raise ValueError("没有唯一成功采集记录：" + source_id)
    record = matches[0]
    if Path(record["filename"]).name != record["filename"]:
        raise ValueError("不允许来源路径越界")
    raw = (directory / record["filename"]).read_bytes()
    if digest(raw) != record["original_sha256"]:
        raise ValueError("来源原件指纹变化：" + source_id)
    return record, raw


def build(*, base=BASE, captures=CAPTURES):
    spec = json.loads((base / "rule-spec.json").read_text(encoding="utf-8"))
    if spec["human_review"] != {"status": "pending", "reviewer": None, "confirmed_package_sha256": None}:
        raise ValueError("该入口只构建待复核包，不能写入人工批准")
    destination = base / "review-pack-v1"
    sources, segments, citations = [], {}, {}
    for selection in spec["sources"]:
        source_id = selection["source_id"]
        record, raw = verified_capture(captures, selection["capture"], source_id)
        rows, _ = parse_html(raw, selection["selector"], min_text_length=1)
        if len(rows) != selection["expected_paragraphs"]:
            raise ValueError("原件段落布局发生变化：" + source_id)
        selected = []
        for ordinal in selection["selected_ordinals"]:
            row = rows[ordinal - 1]
            key = source_id + "-P" + str(ordinal)
            segments[key] = row["text"]
            selected.append({"segment_id": key, **row, "text_sha256": digest(row["text"].encode())})
        source = {"source_id": source_id, "url": record["url"], "title": record["title"],
            "original_sha256": record["original_sha256"], "original_file": "originals/" + record["filename"],
            "checked_at": record["checked_at"], "published_at": selection["published_at"],
            "effective_from": selection["effective_from"],
            "effective_from_basis": selection.get("effective_from_basis"),
            "region": "national" if source_id.startswith("LAW") else "南京市",
            "parsed_paragraphs": len(rows), "selector": selection["selector"], "segments": selected,
            "scope_review": "machine_source_comparison_not_human_business_review"}
        sources.append(source)
        put(destination / source["original_file"], raw)
    rules = []
    for proposal in spec["rules"]:
        segment_id = proposal["source_id"] + "-P" + str(proposal["ordinal"])
        text = segments[segment_id]
        quote = proposal["required_text"]
        if text.count(quote) != 1:
            raise ValueError("引用不唯一或原文不支持规则：" + proposal["id"])
        start = text.index(quote)
        citation_id = "CITE-" + proposal["id"]
        citations[citation_id] = {"id": citation_id, "source_id": proposal["source_id"],
            "segment_id": segment_id, "start": start, "end": start + len(quote), "quote": quote,
            "offset_unit": "unicode_code_points", "context_sha256": digest(text.encode())}
        rules.append({**proposal, "citation_ids": [citation_id]})
    record, raw = verified_capture(captures, "nanjing-national-law-v1", "LAW-NPC-GAZETTE-MIRROR")
    visual = json.loads((captures / "law-visual-review.json").read_text(encoding="utf-8"))
    if visual["original_sha256"] != digest(raw) or visual["actor_kind"] != "ai_visual_review":
        raise ValueError("公报视觉核对记录没有绑定实际原件")
    put(destination / "originals/law-gazette-2011-pages651-654.pdf", raw)
    put(destination / "law-visual-review.json", encoded(visual))
    pack = {"schema": 1, "kind": spec["kind"], "scope": spec["scope"], "sources": sources,
        "citations": list(citations.values()), "rules": rules, "human_review": spec["human_review"],
        "knowledge_published": False, "development_tasks": spec["development_tasks"], "holdout": spec["holdout"],
        "law_crosscheck": {"source_id": record["source_id"], "url": record["url"], "original_sha256": digest(raw),
            "original_file": "originals/law-gazette-2011-pages651-654.pdf", "pdf_page": 2,
            "printed_page": 652, "articles": ["第十一条", "第十二条"], "visual_check": "law-visual-review.json"}}
    put(destination / "rules-and-sources.json", encoded(pack))
    readme = """# 南京身份证期满换领：真实来源规则复核包 v1

本包含4份官方网页原件、政府保存的全国人大公报PDF、16条待人工复核规则、完整原文上下文及引用定位。
主分支为南京办理、江苏户籍、成年完全民事行为能力人本人期满换领；异常分支不得自动套用。
rules-and-sources.json 是机器阅读来源后的候选，human_review=pending、knowledge_published=false。
2条多文档问题为公开开发案例，不是独立留出测试。没有调用模型API或伪造人工批准。

优先检查：期满网办排除；监护例外；普通20元与减免边界；20工作日与法定60日区别；
旧证未超期可用、领取新证时交回；考生绿色通道不等于3天；临时证仅给出法律申请路径。
南京问答生效日期未知；公布日期和抓取日期分开保存。法律2011修正施行时间由决定条文核对。

公报为扫描原件，未运行OCR；AI视觉核对页码/条文记录见 law-visual-review.json，不代表人工业务批准。
原件为法定文件或公开政务事实的研究归档，网页设计和外链资产不作为本项目作品。保留官方来源及指纹。
"""
    put(destination / "README.md", readme.encode())
    files = [{"path": str(p.relative_to(destination)).replace("\\", "/"), "bytes": p.stat().st_size,
              "sha256": digest(p.read_bytes())} for p in sorted(destination.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    manifest = {"schema": 1, "rule_count": len(rules), "source_count": len(sources), "file_count": len(files),
        "formal_business_review": False, "knowledge_published": False, "files": files,
        "rules_sha256": digest((destination / "rules-and-sources.json").read_bytes())}
    put(destination / "manifest.json", encoded(manifest))
    zip_path = base / "nanjing-id-renewal-review-v1.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(destination.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(str(p.relative_to(base)).replace("\\", "/"), date_time=(2026,10,19,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, p.read_bytes())
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("交付ZIP完整性检查未通过")
    result = {"stage": "01_source_rule_review_package", "status": "machine_review_ready_human_pending",
        "rules": len(rules), "citations": len(citations), "html_sources": len(sources), "statute_pdf": 1,
        "model_api_calls": 0, "formal_business_approval": False, "knowledge_published": False,
        "zip": str(zip_path.relative_to(ROOT)).replace("\\", "/"), "zip_sha256": digest(zip_path.read_bytes()),
        "rules_sha256": manifest["rules_sha256"]}
    output = ROOT / "evidence/real-matter-closure/stage-01-source-rules.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded(result))
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))

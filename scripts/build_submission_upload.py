"""Stage the preliminary-round upload ZIP named after the official template; nothing is uploaded.

The template names the Word "创新赛-开发设计文档-学校-队名-队长姓名-手机号" and the
upload control accepts only .zip/.rar. School, team, leader and phone are read
from the cover table of the captain-edited Word, so contact details never enter
the repository or a command line, and the printed summary masks the phone.
Output goes to artifacts/submission-<stamp>/ (ignored by Git). A cover cell
still marked 待补 is reported as a blocker and the staged ZIP stays a draft.
Internal team documents, private runs and reference answers are not included.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from build_material_review_bundle import check

ROOT = Path(__file__).resolve().parents[1]
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
KNOWLEDGE = ROOT / "artifacts/knowledge-20261105/frozen-knowledge-20261105.zip"
CAPTURES = ROOT / "docs/competition/assets/screenshots/current-20261105"
MCP_FILES = ("src/gov_mcp/ontology_crossdoc_preview_server.py", "src/gov_mcp/successor_update_server.py",
             "src/gov_mcp/frozen_qa_server.py")


def cover(docx):
    """Return the cover fields and every cell still marked 待补."""
    with zipfile.ZipFile(docx) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    rows = [["".join(t.text or "" for t in cell.iter(W + "t")).strip() for cell in row.iter(W + "tc")]
            for row in root.iter(W + "tr")]
    value = {cells[0]: cells[1] for cells in rows if len(cells) >= 2}
    leader = [re.sub(r"（队长）$", "", c) for cells in rows for c in cells if c.endswith("（队长）")]
    fields = {"school": value.get("学校名称", ""), "team": value.get("队伍名称", ""),
              "leader": leader[0] if len(leader) == 1 else "", "phone": value.get("队长联系电话", "")}
    if not all(fields.values()) or not re.fullmatch(r"1\d{10}", fields["phone"]):
        raise ValueError("COVER_FIELDS_INCOMPLETE")
    pending = [cells[0] for cells in rows if len(cells) >= 2 and "待补" in cells[1]]
    return fields, pending


def members(stamp, code_stamp, official, base):
    review = ROOT / f"artifacts/material-review-{stamp}"
    exports = ROOT / "config/exports/frozen-qa-20261105"
    mapping = {
        f"{base}.docx": official,
        f"技术附录（含Nexent智能体配置说明）-{stamp}.docx": review / f"技术附录-{stamp}.docx",
        f"完整代码-ict-topic3-complete-source-{code_stamp}.zip":
            ROOT / f"artifacts/complete-source-{code_stamp}/ict-topic3-complete-source-{code_stamp}.zip",
        "Nexent智能体配置/南京-本体跨文档推理-Agent-Skill导出.zip": ROOT / "artifacts/competition-20261021/nexent-ontology-crossdoc-native-export.zip",
        "Nexent智能体配置/河南-三模态归档推理-Agent-Skill导出.zip": ROOT / "artifacts/competition-20261022/nexent-henan-multimodal-native-export.zip",
        "Nexent智能体配置/河南-后继资料更新-Agent-Skill导出.zip": ROOT / "artifacts/competition-20261105/nexent-successor-update-native-export.zip",
        "Nexent智能体配置/更新智能体组接线说明.md": ROOT / "config/exports/successor-update-native-20261105/README.md",
        "Nexent智能体配置/38号-冻结资料问答-qa-agent.json": exports / "qa-agent.json",
        "Nexent智能体配置/39号-知识库问答-kb-agent.json": exports / "kb-agent.json",
        "Nexent智能体配置/接线与复现说明.md": exports / "README.md",
        "知识库/frozen-knowledge-20261105.zip": KNOWLEDGE,
    }
    mapping.update({"示例截图/" + path.name: path for path in sorted(CAPTURES.glob("*")) if path.is_file()})
    return mapping


def kind(name):
    if name.endswith(".docx"):
        return "设计文档"
    if name.startswith("完整代码"):
        return "完整代码附件"
    if name.startswith("Nexent智能体配置/"):
        return "接线说明" if name.endswith(".md") else "Nexent智能体导出"
    if name.startswith("知识库/"):
        return "知识库文件"
    return "脱敏截图" if name.endswith(".png") else "截图清单"


def guide(payload, base, code_name):
    lines = [f"# 附件说明\n", f"正文：{base}.docx。技术附录第7章为完整的Nexent智能体配置说明（模型、工具、知识库、调用关系、调试过程、问答截图及同步提交文件）。\n",
             "| 文件 | 内容 | 字节 | SHA-256 |", "| --- | --- | --- | --- |"]
    for name, raw in payload.items():
        lines.append(f"| {name} | {kind(name)} | {len(raw)} | `{hashlib.sha256(raw).hexdigest()}` |")
    lines += ["", f"MCP服务源码位于{code_name}内：" + "、".join(f"`{path}`" for path in MCP_FILES) + "，启动与端口见附录表7-2及包内复现说明。",
              "知识库为7份公开政务资料的冻结Markdown与581块清单，保留原网址、修订、散列和定位；政府原文权利归发布机关，仅用于本作品评审复核。",
              "智能体JSON均为禁用、未发布的草稿导出；账号、令牌、数据库、原始会话和私有运行数据不在附件中。", ""]
    return "\n".join(lines).encode("utf-8")


def build(stamp, code_stamp, official=None):
    official = Path(official) if official else ROOT / f"artifacts/material-review-{stamp}/开发设计文档-河南大学-不拿国奖就旷课-{stamp}.docx"
    fields, pending = cover(official)
    base = f"创新赛-开发设计文档-{fields['school']}-{fields['team']}-{fields['leader']}-{fields['phone']}"
    payload = {}
    for name, path in members(stamp, code_stamp, official, base).items():
        if not path.is_file():
            raise ValueError("MISSING_MEMBER_SOURCE: " + name)
        payload[name] = path.read_bytes()
        check(name, payload[name])
    payload["附件说明.md"] = guide(payload, base, f"完整代码-ict-topic3-complete-source-{code_stamp}.zip")
    if len(base) + 4 > 250 or max(name.count("/") for name in payload) + 1 > 5:
        raise ValueError("UPLOAD_NAME_OR_DEPTH_LIMIT")
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in payload.items():
            archive.writestr(name, raw)
    raw_zip = stream.getvalue()
    with zipfile.ZipFile(BytesIO(raw_zip)) as archive:
        if archive.testzip() is not None or any(archive.read(n) != payload[n] for n in payload):
            raise ValueError("ARCHIVE_ROUNDTRIP_FAILED")
    out = ROOT / f"artifacts/submission-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{base}.zip").write_bytes(raw_zip)
    digest = hashlib.sha256(raw_zip).hexdigest()
    (out / "PACKAGE-SHA256.txt").write_text(f"{digest}  {base}.zip\n", encoding="utf-8")
    masked = base.replace(fields["phone"], fields["phone"][:3] + "****" + fields["phone"][-4:])
    return {"status": "DRAFT_BLOCKED" if pending else "READY_FOR_CAPTAIN_UPLOAD", "blockers": pending,
            "upload_zip": masked + ".zip", "members": len(payload), "archive_bytes": len(raw_zip), "archive_sha256": digest,
            "uploaded": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--code-stamp", help="Complete-source attachment to include; defaults to --stamp")
    parser.add_argument("--official", help="Captain-edited official Word to use instead of the review copy")
    args = parser.parse_args()
    print(json.dumps(build(args.stamp, args.code_stamp or args.stamp, args.official), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

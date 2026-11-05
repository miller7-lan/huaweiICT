"""Assemble the local three-person material review bundle; it is never published.

The bundle carries the captain-edited official Word, which holds team contact
details, so it is written only to artifacts/material-review-<stamp>/ (ignored by
Git). Every other member is copied byte for byte from the repository or a dated
artifact. No network, model, service or upload is involved.
"""
import argparse
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import zipfile

from build_competition_package import inspect_content, safe_name

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "docs/competition/材料总清单与验收缺口-2026-11-04.md",
    "docs/competition/评测先导与方法B候选-2026-11-05.md",
    "docs/competition/30题人工盲评分工-2026-11-05.md",
    "docs/competition/知识库与材料接续完成记录-2026-11-05.md",
    "docs/competition/其他电脑复现与同学后续任务-2026-11-03.md",
    "docs/competition/完整代码附件说明-2026-11-03.md",
    "docs/competition/复现与交付说明-2026-10-21.md",
    "docs/competition/真实资产与规范对齐表-2026-10-21.md",
    "docs/competition/规范条款映射复核-程千城-2026-11-03.md",
    "docs/competition/扫描件AI复核-2026-11-03.md",
    "docs/competition/独立评测执行方案-2026-10-21.md",
    "docs/competition/Skill工作流模板说明-2026-10-30.md",
    "docs/competition/自由提问入口与评测运行说明-2026-10-30.md",
    "docs/competition/远程更新与整合记录-2026-11-02.md",
    "docs/competition/chapters/Nexent两版导入复现与兼容性-队友核对稿-20261102.md",
    "evaluation/lowresource-20261030/units.json",
)
TEXT_SUFFIXES = {".md", ".json", ".txt", ".py", ".yml", ".toml", ".lock"}


def readme(stamp, code_stamp, official_pages=11):
    day = f"{int(stamp[4:6])}月{int(stamp[6:8])}日"
    return (f"# {day}材料审阅包\n\n"
            "学校：河南大学；队名：不拿国奖就旷课。\n"
            "先读docs/competition/初赛提交清单与剩余事项-2026-11-05.md。\n"
            f"01是保留队长最新填写内容的官方正文，其中代码附件文件名已同步为{code_stamp}版，共{official_pages}页。\n"
            f"02是技术附录（含Nexent智能体配置说明）；03是{code_stamp}版完整自研代码附件（数量见包内清单）；04是原始平台导出（修订版含河南后继更新组）。\n"
            "06含新增问答草稿JSON、知识库文件包和复现说明，07是本次脱敏截图。\n"
            "docs/competition/评测先导与方法B候选-2026-11-05.md记录8题开发先导与方法B候选存证，先导不是正式成绩。\n"
            "docs/competition/低资源构建架构对照-2026-11-05.md是机器侧四种候选构建架构的隔离对照，不是人工计时。\n"
            "三位成员先通读正文，再按异机复现指南交回真实记录；已有材料不必重新写。\n"
            "本包含队长填写的联系方式，仅用于队内审阅和后续由队长决定的正式提交，不公开推送。\n"
            "06的知识库含政府归档文本，仅用于本地审阅；正式再分发须先确认许可。凭据、数据库、原始日志、独立参考答案和官方空白模板未装入。\n"
            "未取得的独立成绩、人工计时和真人复核不预填；未执行赛事上传。\n"
            "部分历史链接依赖完整仓库：https://github.com/miller7-lan/huaweiICT/tree/codex/sync-m1-m2\n"
            "文件散列见manifest.json和SHA256SUMS.txt；ZIP整体散列在同目录PACKAGE-SHA256.txt。\n").encode("utf-8")


def sources(stamp, code_stamp):
    for tag in (stamp, code_stamp):
        if not re.fullmatch(r"[0-9]{8}(?:-r[1-9][0-9]*)?", tag):
            raise ValueError("STAMP_MUST_BE_YYYYMMDD_WITH_OPTIONAL_REVISION")
    out = ROOT / f"artifacts/material-review-{stamp}"
    return out, {
        "01-官方正文.docx": out / f"开发设计文档-河南大学-不拿国奖就旷课-{stamp}.docx",
        "02-技术附录.docx": ROOT / "docs/competition/开发设计文档-20261103.docx",
        "03-完整自研代码.zip": ROOT / f"artifacts/complete-source-{code_stamp}/ict-topic3-complete-source-{code_stamp}.zip",
        "04-原始平台导出/南京-Agent-Skill.zip": ROOT / "artifacts/competition-20261021/nexent-ontology-crossdoc-native-export.zip",
        "04-原始平台导出/河南-Agent-Skill.zip": ROOT / "artifacts/competition-20261022/nexent-henan-multimodal-native-export.zip",
        **{name: ROOT / name for name in DOCS},
        "05-来源取得要求.json": ROOT / "artifacts/competition-20261021/source-requirements.json",
        "LICENSE": ROOT / "LICENSE",
    }


def revised_sources():
    """Optional additions for the Nov 5 revision; legacy bundles retain their scope."""
    mapping = {
        "06-自由问答与知识库/qa-agent.json": ROOT / "config/exports/frozen-qa-20261105/qa-agent.json",
        "06-自由问答与知识库/kb-agent.json": ROOT / "config/exports/frozen-qa-20261105/kb-agent.json",
        "06-自由问答与知识库/frozen-knowledge-20261105.zip": ROOT / "artifacts/knowledge-20261105/frozen-knowledge-20261105.zip",
        "06-自由问答与知识库/接线与复现说明.md": ROOT / "config/exports/frozen-qa-20261105/README.md",
        "04-原始平台导出/河南后继更新-Agent-Skill.zip": ROOT / "artifacts/competition-20261105/nexent-successor-update-native-export.zip",
        "04-原始平台导出/河南后继更新-接线说明.md": ROOT / "config/exports/successor-update-native-20261105/README.md",
        **{name: ROOT / name for name in (
            "docs/competition/初赛提交清单与剩余事项-2026-11-05.md",
            "docs/competition/低资源构建架构对照-2026-11-05.md",
            "evidence/lowresource-20261105/architecture-comparison-20261105T074630Z.json")},
    }
    captures = ROOT / "docs/competition/assets/screenshots/current-20261105"
    mapping.update({"07-本次截图/" + path.name: path for path in sorted(captures.glob("*")) if path.is_file()})
    return mapping


def check(name, raw):
    """Credential-pattern checks on text members, including text inside nested ZIPs."""
    if Path(name).suffix.lower() in TEXT_SUFFIXES or name == "LICENSE":
        inspect_content(name, raw)
    if name.endswith(".zip"):
        with zipfile.ZipFile(BytesIO(raw)) as nested:
            if nested.testzip() is not None:
                raise ValueError("NESTED_CRC_FAILED: " + name)
            for inner in nested.namelist():
                if Path(inner).suffix.lower() in TEXT_SUFFIXES:
                    inspect_content(name + "!" + inner, nested.read(inner))


def build(stamp, code_stamp, official_pages=11):
    out, mapping = sources(stamp, code_stamp)
    if "-r" in stamp:
        mapping.update(revised_sources())
        mapping["02-技术附录.docx"] = out / f"技术附录-{stamp}.docx"
    payload = {}
    for name, path in mapping.items():
        safe_name(name)
        if not path.is_file():
            raise ValueError("MISSING_MEMBER_SOURCE: " + name)
        payload[name] = path.read_bytes()
        check(name, payload[name])
    payload["先读我.md"] = readme(stamp, code_stamp, official_pages)
    manifest = {"date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "package_tag": stamp, "scope": "local_team_material_review_not_official_submission",
                "contains_team_contact_information": True,
                "files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in payload.items()]}
    sums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in manifest["files"]).encode("utf-8")
    payload["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload["SHA256SUMS.txt"] = sums
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in payload.items():
            archive.writestr(name, raw)
    raw_zip = stream.getvalue()
    with zipfile.ZipFile(BytesIO(raw_zip)) as archive:
        if archive.testzip() is not None or any(archive.read(n) != payload[n] for n in payload):
            raise ValueError("ARCHIVE_ROUNDTRIP_FAILED")
    archive_path = out / f"ict-topic3-material-review-{stamp}.zip"
    archive_path.write_bytes(raw_zip)
    for name in ("manifest.json", "SHA256SUMS.txt", "先读我.md"):
        (out / name).write_bytes(payload[name])
    digest = hashlib.sha256(raw_zip).hexdigest()
    (out / "PACKAGE-SHA256.txt").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return {"status": "PASS_LOCAL_MATERIAL_REVIEW_PACKAGE_INTEGRITY", "date": manifest["date"],
            "payload_files": len(manifest["files"]), "archive_members": len(payload), "archive_bytes": len(raw_zip),
            "archive_sha256": digest, "code_attachment": mapping["03-完整自研代码.zip"].name,
            "code_attachment_sha256": hashlib.sha256(payload["03-完整自研代码.zip"]).hexdigest(),
            "contains_team_contact_information": True, "public_upload": False, "official_submission": False,
            "model_calls": 0, "business_acceptance": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--code-stamp", help="Dated complete-source attachment to include; defaults to --stamp")
    parser.add_argument("--official-pages", type=int, default=11, help="Page count of the official Word as exported for review")
    args = parser.parse_args()
    print(json.dumps(build(args.stamp, args.code_stamp or args.stamp, args.official_pages), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

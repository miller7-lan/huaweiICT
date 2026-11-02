"""Assemble existing public deliverables for team review, without deploying or publishing.

Government originals, private runtime, independent answers and official template
originals are deliberately outside this exact file list. The engineering ZIP
must already have been built by build_competition_package.py.
"""
from pathlib import Path
from io import BytesIO
import argparse
import hashlib
import json
import zipfile

from build_competition_package import checked_file, inspect_content, safe_name

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/submission-review-20261102"
ARCHIVE = OUTPUT / "ict-topic3-review-20261102.zip"
FILES = (
    "LICENSE",
    "docs/competition/开发设计文档-20261102.docx",
    "docs/competition/chapters/README.md",
    *(f"docs/competition/chapters/第{n}章-陈子炫.md" for n in (4, 6, 7, 8)),
    "docs/competition/assets/screenshots/README.md",
    *("docs/competition/assets/screenshots/" + name for name in (
        "S01a-问答智能体基本设置-20261030.jpg", "S01b-问答智能体模型与提示词-20261030.jpg",
        "S02-MCP工具绑定-20261030.jpg", "S04a-跨文档问答结论与引用-20261030.jpg",
        "S04b-跨文档问答未知项与说明-20261030.jpg", "S04c-问答调用过程-20261030.jpg",
        "S05a-现行效力问题停止作答-20261030.jpg", "S05b-停止作答未知项与说明-20261030.jpg")),
    "docs/competition/初赛提交与合稿框架-2026-10-30.md",
    "docs/competition/复现与交付说明-2026-10-21.md",
    "docs/competition/自由提问入口与评测运行说明-2026-10-30.md",
    "evidence/design-document-20261102/verification.json",
    "config/exports/ontology-crossdoc-native-20261021/agent.json",
    "config/exports/henan-multimodal-native-20261022/agent.json",
    "artifacts/competition-20261021/nexent-ontology-crossdoc-native-export.zip",
    "artifacts/competition-20261022/nexent-henan-multimodal-native-export.zip",
    "artifacts/competition-20261021/ict-topic3-public-engineering-20261021.zip",
    "artifacts/competition-20261021/manifest.json",
    "artifacts/competition-20261021/source-requirements.json",
    "artifacts/competition-20261021/verification.json",
    "scripts/build_submission_review_bundle.py",
)
NOTICE = """# 第三题团队审阅包

这是2026年11月2日整理的团队审阅副本，不是正式提交或验收通过声明。
Word修订版33页，负责人13个小节及8张既有截图已合入，队友黄色待补保留。
工程ZIP为当前白名单源码，两个原生ZIP是各自日期的原始平台导出，互不替代。
导入新租户后须重新绑定模型、工具、Skill及权限；旧账号与预算不得复制。
冻结问答助手已有接线脚本和读回，尚无该助手独立的原始平台导出ZIP。
原生知识库导入、真人复核、独立评测、低资源人工记录及干净环境回执仍待完成。
官方模板、作品提交日时、上传格式及学校队名尚未核定，不自动上传赛事。
来源原件、官方模板原件、凭据、运行库、原始私有日志和独立参考答案不在本包内。
文档链接可能指向仓库其他证据；完整历史请从项目仓库取得。
项目仓库：https://github.com/miller7-lan/huaweiICT
重新打包时先将嵌套工程ZIP解压到本审阅包根目录，再执行：
python scripts/build_submission_review_bundle.py
复核已有包执行同一命令并添加 --verify-only，不部署服务或调用模型。
完整性与已知凭据特征检查不证明全部内容已获业务批准或第三方再分发许可。
""".encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def scan(name, raw, depth=0):
    safe_name(name)
    if depth > 4:
        raise ValueError("ARCHIVE_NESTING_LIMIT")
    suffix = Path(name).suffix.lower()
    if suffix in {".zip", ".docx"}:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip() is not None:
                raise ValueError("ARCHIVE_MEMBERS_OR_CRC_INVALID")
            if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("EXPANDED_ARCHIVE_TOO_LARGE")
            for child in names:
                if child.endswith("/"):
                    continue
                scan(child, archive.read(child), depth + 1)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        return  # Previously reviewed screenshots; no OCR privacy certification.
    else:
        inspect_content(name, raw)


def verify():
    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = json.loads(archive.read("review-manifest.json"))
        expected = {entry["path"]: entry for entry in manifest["files"]}
        if len(manifest["files"]) != len(expected) or len(expected) != len(FILES) or set(expected) != set(FILES):
            raise ValueError("EXACT_ALLOWLIST_REQUIRED")
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(FILES) | {"review-manifest.json", "READ-ME.md"}:
            raise ValueError("BUNDLE_MEMBERS_DIFFER")
        for name in names:
            raw = archive.read(name)
            scan(name, raw)
            if name in expected and (sha(raw) != expected[name]["sha256"] or len(raw) != expected[name]["bytes"]):
                raise ValueError("BUNDLE_PAYLOAD_HASH_MISMATCH")
        if archive.read("READ-ME.md") != NOTICE or archive.testzip() is not None:
            raise ValueError("NOTICE_OR_CRC_MISMATCH")
    return {"status": "PASS_TEAM_REVIEW_BUNDLE_INTEGRITY_ONLY", "files": len(FILES),
            "archive_sha256": sha(ARCHIVE.read_bytes()), "business_acceptance": False,
            "official_submission": False, "network_calls": 0, "model_calls": 0}


def build():
    payload = {name: checked_file(name).read_bytes() for name in FILES}
    for name, raw in payload.items():
        scan(name, raw)
    manifest = {"date": "2026-11-02", "kind": "team_review_not_final_submission",
                "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)} for name, raw in payload.items()]}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for target in (OUTPUT, ARCHIVE, OUTPUT / "verification.json"):
        if target.is_symlink() or not target.resolve().is_relative_to(ROOT):
            raise ValueError("EXTERNAL_OUTPUT_FORBIDDEN")
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in {**payload, "READ-ME.md": NOTICE,
                          "review-manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode()}.items():
            archive.writestr(name, raw)
    if any(checked_file(name).read_bytes() != raw for name, raw in payload.items()):
        raise ValueError("SOURCE_CHANGED_DURING_BUILD")
    report = verify()
    (OUTPUT / "verification.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify_only else build()))

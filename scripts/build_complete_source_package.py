"""Build the complete first-party source attachment, without executing that code.

Selection uses Git's tracked and nonignored untracked inventory. Government
originals, private runtime, reference answers and third-party dependencies are
not source payloads. No network, model, service or publishing operation is used.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import stat
import subprocess
import zipfile

from build_competition_package import (
    checked_file, encoded, inspect_content, safe_name, sha, source_requirements,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/complete-source-20261103"
ARCHIVE = OUTPUT / "ict-topic3-complete-source-20261103.zip"
SOURCE_ROOTS = {"src", "scripts", "tests", "config", "deploy", "models"}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".ps1", ".sh", ".html", ".css"}
ROOT_FILES = {
    ".dockerignore", ".gitattributes", ".gitignore", "AGENTS.md", "LICENSE",
    "README.md", "Dockerfile", "Dockerfile.knowledge", "pyproject.toml",
    "requirements.lock", "requirements-knowledge.lock",
    "compose.asset-query.yml", "compose.cloud-proxy.yml", "compose.knowledge.yml",
    "compose.m1.yml", "compose.m2.yml", "compose.mcp.yml",
}
# Source HTML in these directories is third-party material, not application UI.
THIRD_PARTY_PREFIXES = (
    "data/", "evidence/public-native-preview/sources/",
    "evidence/real-business-first/sources/",
)
SUPPORT_FILES = {
    "data/knowledge/update-test-pairs/henan-id-time.json",
    "docs/competition/完整代码附件说明-2026-11-03.md",
    "docs/competition/复现与交付说明-2026-10-21.md",
    "docs/competition/当前Nexent配置与证据说明-2026-10-21.md",
    "docs/competition/Skill工作流模板说明-2026-10-30.md",
    "docs/competition/自由提问入口与评测运行说明-2026-10-30.md",
    "docs/competition/远程更新与整合记录-2026-11-02.md",
}
META = {"source-manifest.json", "SHA256SUMS.txt", "SOURCE-ATTACHMENT.md", "source-requirements.json"}
NOTICE = """# 第三题完整自研代码附件

学校：河南大学；队名：不拿国奖就旷课。
范围是本次本地工作区主工程的全部自研源代码，不是整个 Git 仓库或正式赛事提交。
先读 docs/competition/完整代码附件说明-2026-11-03.md。
完整目录：src、scripts、tests、config、deploy、models文本配方，及仓库其他位置的自研代码。
保留全部脚本，包括历史采集、实验、部署和模型运行入口；打包绝不执行这些脚本。
历史入口可能写私有目录、调用模型或改变平台，不应批量运行。不能复用旧预算和审批。
第三方官方HTML、政府原件及其提取目录、独立参考答案、账号、密钥、运行库、日志、
模型权重、Nexent/vendor源码和依赖二进制不在本包内。应按合法来源另取匹配版本。
南京另一套实现仍在feat/real-matter-closure分支；本附件不混入该分支的独立工程副本。
本包不包含全部历史证据和数据夹具，部分检查需另取匹配且获准的资料才能执行。
代码和配置完整不等于数据与环境齐备，不证明干净环境业务复现或独立效果达标。
不改变暂停、来源版本、隐私权限、人工确认或模型禁用状态，不自动部署、发布或推送。
项目代码许可证见LICENSE；政府及第三方资料不由该许可证授权。

核验本包：解压到新目录后运行
python scripts/build_complete_source_package.py --verify-only --archive <原ZIP路径>
仅核对清单、散列、源码语法和已知凭据特征，不运行程序或发出网络请求。
重新生成需在有Git清单的项目工作区执行同一脚本，不带--verify-only。
""".encode("utf-8")


def inventory() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted(set(filter(None, result.stdout.decode("utf-8").split("\0"))))


def select(names: list[str]) -> tuple[list[str], list[str]]:
    selected, excluded_third_party = set(ROOT_FILES) | set(SUPPORT_FILES), []
    for name in names:
        if name.split("/", 1)[0] in SOURCE_ROOTS:
            safe_name(name)
            selected.add(name)
        elif Path(name).suffix.lower() in CODE_SUFFIXES:
            if name.startswith(THIRD_PARTY_PREFIXES):
                excluded_third_party.append(name)
            else:
                safe_name(name)
                selected.add(name)
        elif "/" not in name and name not in ROOT_FILES:
            raise ValueError("UNCLASSIFIED_ROOT_FILE: " + name)
    return sorted(selected), sorted(excluded_third_party)


def check_payload(name: str, raw: bytes) -> None:
    safe_name(name)
    inspect_content(name, raw)
    if name.endswith(".py"):
        # Compile only: never import or execute application/experiment code.
        compile(raw, name, "exec", dont_inherit=True)


def verify_bytes(raw_zip: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(raw_zip)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("INVALID_ARCHIVE_MEMBERS_OR_CRC")
        if any(info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)
               for info in archive.infolist()):
            raise ValueError("SYMLINK_MEMBER_FORBIDDEN")
        if sum(info.file_size for info in archive.infolist()) > 64 * 1024 * 1024:
            raise ValueError("EXPANDED_ARCHIVE_TOO_LARGE")
        for name in names:
            safe_name(name)
        manifest = json.loads(archive.read("source-manifest.json"))
        expected = {item["path"]: item for item in manifest["files"]}
        if len(expected) != len(manifest["files"]) or set(names) != set(expected) | META:
            raise ValueError("EXACT_MANIFEST_REQUIRED")
        if set(manifest["source_inventory"]) != set(expected):
            raise ValueError("COVERAGE_INVENTORY_MISMATCH")
        for name, entry in expected.items():
            raw = archive.read(name)
            if len(raw) != entry["bytes"] or sha(raw) != entry["sha256"]:
                raise ValueError("PAYLOAD_HASH_MISMATCH: " + name)
            check_payload(name, raw)
        sums = "".join(f"{expected[n]['sha256']}  {n}\n" for n in sorted(expected)).encode()
        if archive.read("SHA256SUMS.txt") != sums or archive.read("SOURCE-ATTACHMENT.md") != NOTICE:
            raise ValueError("CHECKSUM_LIST_OR_NOTICE_MISMATCH")
        for name in META:
            inspect_content(name, archive.read(name))
        requirements = json.loads(archive.read("source-requirements.json"))
        if requirements.get("source_originals_included") != 0:
            raise ValueError("UNEXPECTED_SOURCE_ORIGINALS")
        for name in ROOT_FILES | SUPPORT_FILES:
            if name not in expected:
                raise ValueError("MISSING_REQUIRED_FILE: " + name)
        py_count = sum(name.endswith(".py") for name in expected)
        return {
            "status": "PASS_SOURCE_ATTACHMENT_INTEGRITY_ONLY", "payload_files": len(expected),
            "archive_members": len(names), "python_syntax_checked": py_count,
            "directory_counts": dict(sorted(Counter(n.split('/')[0] for n in expected).items())),
            "archive_sha256": sha(raw_zip), "business_acceptance": False,
            "clean_environment_reproduction": False, "official_submission": False,
            "network_calls": 0, "model_calls": 0,
        }


def build() -> dict:
    names, third_party = select(inventory())
    payload = {name: checked_file(name).read_bytes() for name in names}
    for name, raw in payload.items():
        check_payload(name, raw)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    manifest = {
        "schema_version": 1, "date": "2026-11-03", "kind": "complete_first_party_source_working_tree",
        "school": "河南大学", "team": "不拿国奖就旷课", "base_git_head": head,
        "snapshot_kind": "working_tree_bytes_not_a_release_commit",
        "source_inventory": names, "excluded_third_party_source_files": third_party,
        "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)}
                  for name, raw in payload.items()],
    }
    metadata = {
        "source-manifest.json": encoded(manifest), "SOURCE-ATTACHMENT.md": NOTICE,
        "source-requirements.json": encoded(source_requirements()),
        "SHA256SUMS.txt": "".join(f"{sha(raw)}  {name}\n" for name, raw in payload.items()).encode(),
    }
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in {**payload, **metadata}.items():
            archive.writestr(name, raw)
    raw_zip = stream.getvalue()
    report = verify_bytes(raw_zip)
    if select(inventory())[0] != names or any(checked_file(n).read_bytes() != raw for n, raw in payload.items()):
        raise ValueError("WORKING_TREE_CHANGED_DURING_BUILD")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for path in [OUTPUT, ARCHIVE, OUTPUT / "verification.json", OUTPUT / "source-manifest.json",
                 OUTPUT / "SHA256SUMS.txt", OUTPUT / "PACKAGE-SHA256.txt"]:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("UNSAFE_OUTPUT")
    ARCHIVE.write_bytes(raw_zip)
    for name in ["source-manifest.json", "SHA256SUMS.txt"]:
        (OUTPUT / name).write_bytes(metadata[name])
    (OUTPUT / "PACKAGE-SHA256.txt").write_text(f"{sha(raw_zip)}  {ARCHIVE.name}\n", encoding="ascii")
    (OUTPUT / "verification.json").write_bytes(encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    if args.verify_only:
        raw = args.archive.read_bytes()
        checksum = args.archive.parent / "PACKAGE-SHA256.txt"
        if checksum.exists() and checksum.read_text("ascii") != f"{sha(raw)}  {args.archive.name}\n":
            raise ValueError("ARCHIVE_CHECKSUM_MISMATCH")
        result = verify_bytes(raw)
    else:
        result = build()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

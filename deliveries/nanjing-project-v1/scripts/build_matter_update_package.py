"""Seal complete update inputs plus read-only verifier and source mirror; no private runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "data/real-matter-closure/nanjing/update-input-package-v1")
    args = parser.parse_args()
    destination = args.destination.resolve(); archive_path = destination.with_suffix(".zip")
    if destination.exists() or archive_path.exists(): parser.error("Existing sealed package preserved; choose a new version")
    files = {}
    for source in (ROOT / "src/gov_mcp").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts and source.suffix in {".py", ".html", ".css", ".js", ".ps1"}:
            files[source.relative_to(ROOT).as_posix()] = source.read_bytes()
    for source in (ROOT / "data/real-matter-closure/nanjing/update-snapshots-v1").rglob("*"):
        if source.is_file(): files[source.relative_to(ROOT).as_posix()] = source.read_bytes()
    for relative in ("scripts/verify_matter_update_inputs.py", "requirements.lock",
        "evidence/real-matter-closure/update-cycle/stage-04-update-live.json"):
        files[relative] = (ROOT / relative).read_bytes()
    files["README.md"] = """# 南京更新输入与离线核对包 v1

包含三组完整输入快照、官方原件、全正文、引用/文件清单、实际HTTP联调回执、只读校验入口及项目公开源代码镜像。
需要Python 3.11或更高版本；本包的离线校验仅用标准库，不需安装依赖、启动Nexent或调用模型。

```powershell
python scripts/verify_matter_update_inputs.py
```

预期：3/3/4份来源，49/49/142段正文；种子本体在来源新增前后均停止完整推导，演化本体形成4条替代证据路径、1个候选结论。
校验仅针对已保存输入，不检查今日官网适用性，不刷新日期，不发布业务知识，不代表独立业务评测。
包内实际运行回执为2026-10-19的四官网观察与Nexent身份HTTP联调：19项通过、模型调用0。
网页当日未观测到变更；这是受控的来源登记新增试验，不是政府历史政策新旧版本。

完整在线活动及原生Agent需要仓库中的准备入口、本机服务和私有角色配置。账户、令牌、运行数据库、模型权重不随包分享。
当前原生Qwen主控收尾仍待完成，OCR由队友处理，规则及正式发布保持待复核；本包不作为完整项目验收。
""".encode()
    manifest = {"schema": 1, "purpose": "nanjing_update_inputs_and_offline_development_comparison",
        "human_business_review": False, "formal_publication": False,
        "files": [{"path": p, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for p, raw in sorted(files.items())]}
    files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    for relative, raw in files.items():
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination): raise ValueError("Package output path escaped destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write(raw)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, raw in sorted(files.items()):
            info = zipfile.ZipInfo(destination.name + "/" + relative, date_time=(2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, raw)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None: raise ValueError("Package ZIP integrity failed")
    print(json.dumps({"files": len(files), "zip": str(archive_path), "bytes": archive_path.stat().st_size,
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(), "model_calls": 0}))


if __name__ == "__main__": main()

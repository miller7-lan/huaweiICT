"""Bootstrap the team demo without M2/private archives, Docker, or a model."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="团队离线工程演示；首次初始化需要Python 3.11+和PyPI网络。")
    parser.add_argument("action", choices=["init", "check", "update", "refresh", "query", "serve"])
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime/team-demo")
    parser.add_argument("--environment", type=Path, default=ROOT / ".runtime/team-demo-env")
    args, extra = parser.parse_known_args()
    if sys.version_info < (3, 11):
        parser.exit(1, "需要Python 3.11或以上版本。\n")
    environment = args.environment.resolve()
    runtime = args.runtime.resolve()
    if runtime == environment or runtime in environment.parents or environment in runtime.parents:
        parser.exit(1, "演示数据目录与依赖环境目录不能相互包含。\n")
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = environment / "team-demo-environment.json"
    try:
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf-8")) != {"project": str(ROOT), "mode": "team-demo-environment-v1"}:
                raise ValueError("该依赖目录属于另一项目；请指定新的 --environment。")
            if not interpreter.is_file():
                raise ValueError("演示依赖环境不完整；请指定新的 --environment 后重新 init。")
        else:
            if args.action != "init":
                raise ValueError("请先运行 python scripts/team_demo.py init 初始化演示。")
            if environment.exists() and any(environment.iterdir()):
                raise ValueError("依赖环境目录非空，拒绝覆盖；请指定新的 --environment。")
            if runtime.exists() and any(runtime.iterdir()):
                raise ValueError("演示目录非空，拒绝覆盖；请选择新的 --runtime。")
            print("正在建立独立Python环境并安装项目依赖；不安装模型或注册计划任务。", flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
            log = environment / "install.log"
            with log.open("w", encoding="utf-8") as stream:
                completed = subprocess.run([str(interpreter), "-m", "pip", "--disable-pip-version-check",
                    "install", "--no-cache-dir", "-e", str(ROOT)], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            if completed.returncode:
                raise ValueError(f"依赖安装失败，详情见 {log}；修复网络后请指定新的 --environment 重试。")
            marker.write_text(json.dumps({"project": str(ROOT), "mode": "team-demo-environment-v1"}), encoding="utf-8")
            print("独立依赖环境已准备好。", flush=True)
        child = subprocess.Popen([str(interpreter), "-m", "gov_mcp.knowledge_demo", args.action,
                                  "--runtime", str(runtime), *extra], cwd=ROOT)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
            code = 0
        raise SystemExit(code)
    except (ValueError, OSError) as error:
        parser.exit(1, f"团队演示准备失败：{error}\n")


if __name__ == "__main__":
    main()

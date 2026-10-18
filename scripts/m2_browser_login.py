"""Log into the real M2 UI with private literals and redact CLI output."""

import argparse
import json
import shutil
import subprocess

from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("citizen", "staff"), default="citizen")
    parser.add_argument("--session", default="m2")
    args = parser.parse_args()
    account = json.loads((RUNTIME / "m2/accounts.json").read_text(encoding="utf-8"))[args.role]
    ensure_private_directory(ROOT / ".playwright-cli")
    ensure_private_directory(RUNTIME / "m2/browser")
    expected = "已核定业务角色：" + ("群众" if args.role == "citizen" else "工作人员")
    source = "async (page) => {\n" + "\n".join([
        f"await page.getByRole('textbox', {{name:'邮箱',exact:true}}).fill({json.dumps(account['email'])});",
        f"await page.getByRole('textbox', {{name:'密码',exact:true}}).fill({json.dumps(account['password'])});",
        "await page.getByRole('button', {name:'登录',exact:true}).click();",
        f"await page.getByText({json.dumps(expected)}, {{exact:true}}).waitFor();",
    ]) + "\n}"
    path = RUNTIME / "m2/browser/login.js"
    path.write_text(source, encoding="utf-8")
    try:
        completed = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli",
            "playwright-cli", "-s=" + args.session, "run-code", "--filename", str(path)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        output = completed.stdout + completed.stderr
        for value in account.values():
            if isinstance(value, str):
                output = output.replace(value, "[PRIVATE]")
        print(output)
        if "### Error" in output or (completed.returncode and "### Result" not in output):
            raise SystemExit(1)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

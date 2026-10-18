"""CLI browser login with private input and redacted output, never credentials in argv."""

import argparse
import json
import shutil
import subprocess

from nexent_api import ROOT, RUNTIME
from private_runtime import ensure_private_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="m1")
    parser.add_argument("--role", default="fresh_citizen")
    parser.add_argument("--workbench", action="store_true")
    args = parser.parse_args()
    credentials = json.loads((RUNTIME / "m1/accounts.json").read_text(encoding="utf-8"))[args.role]
    ensure_private_directory(ROOT / ".playwright-cli")
    ensure_private_directory(RUNTIME / "m1/browser")
    email_name = "邮箱" if args.workbench else "* 邮箱地址 * 邮箱地址"
    password_name = "密码" if args.workbench else "* 密码 * 密码"
    button_name = "登录" if args.workbench else "登 录"
    source = "async (page) => {\n" + "\n".join([
        f"await page.getByRole('textbox', {{name:{json.dumps(email_name)},exact:true}}).fill({json.dumps(credentials['email'])});",
        f"await page.getByRole('textbox', {{name:{json.dumps(password_name)},exact:true}}).fill({json.dumps(credentials['password'])});",
        f"await page.getByRole('button', {{name:{json.dumps(button_name)},exact:true}}).click();",
        "await page.getByRole('heading', {name:'事项协作工作台',exact:true}).waitFor();" if args.workbench else
        "await page.getByRole('dialog', {name:'登录',exact:true}).waitFor({state:'hidden'});",
    ]) + "\n}"
    path = RUNTIME / "m1/browser/login.js"
    path.write_text(source, encoding="utf-8")
    try:
        completed = subprocess.run([shutil.which("npx.cmd") or "npx", "--yes", "--package", "@playwright/cli",
            "playwright-cli", "-s=" + args.session, "run-code", "--filename", str(path)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        output = completed.stdout + completed.stderr
        for value in credentials.values():
            if isinstance(value, str):
                output = output.replace(value, "[PRIVATE]")
        # CLI prints the evaluated function; private literals are redacted above.
        print(output)
        if completed.returncode or "### Error" in output:
            raise SystemExit(1)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

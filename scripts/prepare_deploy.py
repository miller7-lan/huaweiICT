"""Prepare pinned official deployment configuration restricted to localhost."""

import json
import re
import secrets
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    vendor = root / "vendor/nexent"
    accounts = json.loads((root / ".runtime/accounts.json").read_text(encoding="utf-8"))
    env = vendor / "deploy/env/.env"
    if not env.exists():
        data = (env.parent / ".env.example").read_text(encoding="utf-8")
        replacements = {"NEXENT_SUPER_ADMIN_PASSWORD": accounts["super_admin"]["password"],
                        "ENABLE_AGENT_WORKBENCH": "false", "APP_VERSION": "v2.7.0"}
        for key in ("ELASTIC_PASSWORD", "NEXENT_POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "DASHBOARD_PASSWORD", "SUPABASE_POSTGRES_PASSWORD", "INVITE_CODE"):
            replacements[key] = secrets.token_urlsafe(24)
        for key, value in replacements.items():
            if re.search(rf"^{key}=", data, re.M):
                data = re.sub(rf"^{key}=.*$", f"{key}={value}", data, flags=re.M)
            else:
                data += f"\n{key}={value}\n"
        env.write_text(data, encoding="utf-8")
    # Official scripts choose these files. Only bind addresses are changed;
    # service definitions, migrations and image selection stay upstream.
    for file in (vendor / "deploy/docker/compose").glob("*.yml"):
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        if not data or not isinstance(data.get("services"), dict):
            continue
        lines, port_indent = [], None
        for line in file.read_text(encoding="utf-8").splitlines(keepends=True):
            indent = len(line) - len(line.lstrip())
            if line.strip() == "ports:":
                port_indent = indent
            elif port_indent is not None and line.strip() and not line.lstrip().startswith("#"):
                if indent <= port_indent:
                    port_indent = None
                else:
                    match = re.match(r"^(\s+-\s+[\"']?)([^\"'\s]+:[^\"'\s]+)(.*)$", line)
                    if match and not match[2].startswith("127.0.0.1:"):
                        value = match[2] if match[2].count(":") == 1 else match[2].split(":", 1)[1]
                        line = match[1] + "127.0.0.1:" + value + match[3] + ("\n" if line.endswith("\n") else "")
            lines.append(line)
        file.write_text("".join(lines), encoding="utf-8")
    print("Official deployment prepared: v2.7.0, private secrets, loopback-only published ports.")


if __name__ == "__main__":
    main()

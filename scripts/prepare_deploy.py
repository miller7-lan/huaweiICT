"""Prepare pinned official deployment configuration restricted to localhost."""

import json
import re
import secrets
import subprocess
from pathlib import Path

import yaml


def port_parts(value):
    # Colons inside Compose substitutions and IPv6 addresses are not separators.
    parts, start, braces, brackets = [], 0, 0, 0
    for index, char in enumerate(value):
        if char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
        elif char == ":" and braces == brackets == 0:
            parts.append(value[start:index])
            start = index + 1
    if braces or brackets:
        raise ValueError("Unbalanced Compose port expression")
    parts.append(value[start:])
    if len(parts) not in (1, 2, 3):
        raise ValueError("Unsupported Compose port expression")
    return parts


def bind_compose_ports(content, legacy=False):
    lines, port_indent = [], None
    for line in content.splitlines(keepends=True):
        indent = len(line) - len(line.lstrip())
        if line.strip() == "ports:":
            port_indent = indent
        elif port_indent is not None and line.strip() and not line.lstrip().startswith("#"):
            if indent <= port_indent:
                port_indent = None
            else:
                match = re.match(r"^(\s+-\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))(.*?)(\n?)$", line)
                if not match:
                    raise ValueError("Only short-form Compose ports are supported by this pinned deployment")
                value = next(v for v in match.group(2, 3, 4) if v is not None)
                if not legacy:
                    port_parts(value)
                if not value.startswith("127.0.0.1:"):
                    if legacy:
                        value = value if value.count(":") == 1 else value.split(":", 1)[1]
                    else:
                        parts = port_parts(value)
                        value = ":".join(parts[-2:]) if len(parts) > 1 else ":" + parts[0]
                    value = "127.0.0.1:" + value
                quote = '"' if match.group(2) is not None else "'" if match.group(3) is not None else ""
                line = match.group(1) + quote + value + quote + match.group(5) + match.group(6)
        lines.append(line)
    return "".join(lines)


def prepare_compose(vendor):
    for file in (vendor / "deploy/docker/compose").glob("*.yml"):
        content = file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not data or not isinstance(data.get("services"), dict):
            continue
        # Recover only our exact previous transformation. Unrelated user edits
        # must not be reset to the upstream checkout.
        relative = file.relative_to(vendor).as_posix()
        original = subprocess.check_output(["git", "-C", str(vendor), "show", f"HEAD:{relative}"]).decode("utf-8")
        if content == bind_compose_ports(original, legacy=True):
            content = original
        rewritten = bind_compose_ports(content)
        yaml.safe_load(rewritten)
        file.write_text(rewritten, encoding="utf-8")


def services_only_script(content):
    # Keep the official deployment intact except its automatic account step.
    pattern = r'(?ms)^create_default_super_admin_user\(\) \{.*?^\}\n(?=\nchoose_image_env\(\))'
    replacement = ('create_default_super_admin_user() {\n'
                   '  echo "Account creation deferred to the user-run scripts/initialize_accounts.ps1."\n'
                   '  return 0\n}\n')
    result, count = re.subn(pattern, replacement, content)
    if count != 1:
        raise ValueError('Pinned deployment account boundary was not found; refusing an automatic account deployment')
    return 'export NEXENT_DEPLOY_CONFIG_MODE=defaults DEPLOYMENT_LANGUAGE=en\n' + result


def disabled_monitoring_env(text):
    if not re.search(r"^ENABLE_TELEMETRY=['\"]?false['\"]?\s*$", text, re.M | re.I):
        return text
    # FastAPI automatic telemetry has its own environment configuration.
    for key in ('OTEL_TRACES_EXPORTER', 'OTEL_METRICS_EXPORTER', 'OTEL_LOGS_EXPORTER'):
        if re.search(rf'^{key}=', text, re.M):
            text = re.sub(rf'^{key}=.*$', key + '=none', text, flags=re.M)
        else:
            text += '\n' + key + '=none\n'
    return text


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--use-source-minio', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    vendor = root / "vendor/nexent"
    if args.use_source_minio:
        record = json.loads((root / 'evidence/minio-source-build.json').read_text(encoding='utf-8'))
        image = 'ict-minio:RELEASE.2023-12-20T01-00-02Z-source'
        if record.get('image') != image or record.get('server_commit') != '7a311a3b666a0e5476fbde24d4413220876d9df2':
            raise ValueError('Verified source build evidence is required before configuring MinIO')
        actual_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        if actual_id != record.get('image_id'):
            raise ValueError('The local MinIO image differs from the recorded source build')
        for profile in ('general', 'mainland'):
            source = vendor / f'deploy/env/image-source.{profile}.env'
            text = source.read_text(encoding='utf-8')
            rewritten, count = re.subn(r'^MINIO_IMAGE=.*$', 'MINIO_IMAGE=' + image, text, flags=re.M)
            if count != 1:
                raise ValueError('Expected one MinIO image in the pinned image-source configuration')
            source.write_text(rewritten, encoding='utf-8')
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
    prepare_compose(vendor)
    source = vendor / 'deploy/docker/deploy.sh'
    service_script = vendor / 'deploy/docker/.services-only.sh'
    service_script.write_text(services_only_script(source.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    monitoring = vendor / 'deploy/env/monitoring.env'
    if monitoring.exists():
        text = monitoring.read_text(encoding='utf-8')
    else:
        text = (monitoring.parent / 'monitoring.env.example').read_text(encoding='utf-8')
    monitoring.write_text(disabled_monitoring_env(text), encoding='utf-8')
    print("Official deployment prepared: v2.7.0, private secrets, loopback-only published ports.")


if __name__ == "__main__":
    main()

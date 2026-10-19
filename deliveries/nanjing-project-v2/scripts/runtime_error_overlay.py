"""Generate a hash-guarded SDK overlay for the blank MCP connection boundary."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA256 = "1a7dee94e05f7d41ffc4f53570fbeb25b402c5ef45172d049e7b4f75edfbb0b5"
TARGET = "/opt/backend/.venv/lib/python3.11/site-packages/nexent/core/agents/run_agent.py"
OVERLAY = ROOT / ".runtime/overlays/run_agent.py"
HELPERS = '''
def _blank_mcp_connection_failure(mcp_host, error):
    """Classify only transport connection failures for the blank MCP service."""
    urls = [host if isinstance(host, str) else host.get("url")
            for host in (mcp_host or []) if isinstance(host, (str, dict))]
    if not urls or len(urls) != len(mcp_host) or any(
            url != "http://gov-blank-mcp:8765/sse" for url in urls):
        return False

    def matches(current, visited):
        if id(current) in visited:
            return False
        visited = visited | {id(current)}
        if isinstance(current, BaseExceptionGroup):
            return bool(current.exceptions) and all(matches(child, visited) for child in current.exceptions)
        if isinstance(current, (httpx.ConnectError, httpx.ConnectTimeout)) or getattr(
                current, "_nexent_mcp_connection_timeout", False):
            return True
        cause = current.__cause__ or current.__context__
        return matches(cause, visited) if cause is not None else False

    return matches(error, set())


'''
MAPPING = '''    if _blank_mcp_connection_failure(mcp_host, error):
        return json.dumps({"status": "ERROR", "items": [], "citations": [],
                           "knowledge_version": None, "message_code": "CONNECTION_TIMEOUT",
                           "data": {"attempts": 1, "phase": "mcp_connection"}}, ensure_ascii=False)
'''


def patched_source(original):
    if hashlib.sha256(original).hexdigest() != BASE_SHA256:
        raise ValueError("SDK source differs from the pinned v2.7.0 error boundary")
    source = original.decode("utf-8")
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_agent_run_error_message")
    lines = source.splitlines(keepends=True)
    # The first body node is the docstring. Keep all existing fallback mappings.
    insert_at = function.body[0].end_lineno
    lines.insert(insert_at, MAPPING)
    lines.insert(function.lineno - 1, HELPERS)
    result = "".join(lines)
    compile(result, TARGET, "exec")
    return result.encode("utf-8")


def prepare_overlay(root=ROOT):
    source = root / "vendor/nexent/sdk/nexent/core/agents/run_agent.py"
    destination = root / ".runtime/overlays/run_agent.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = patched_source(source.read_bytes())
    destination.write_bytes(data)
    return destination, hashlib.sha256(data).hexdigest()


def mount_overlay(content, source):
    # Only this known SDK module is mounted; official source and SQL stay intact.
    mount = f'      - "{source.as_posix()}:{TARGET}:ro" # ICT connection error overlay\n'
    if mount in content:
        return content
    anchor = "  nexent-runtime:\n"
    start = content.find(anchor)
    if start < 0:
        return content
    end = content.find("\n  nexent-", start + len(anchor))
    end = len(content) if end < 0 else end
    pos = content.find("    volumes:\n", start, end)
    if pos < 0:
        raise ValueError("Pinned runtime volume boundary is missing")
    pos += len("    volumes:\n")
    return content[:pos] + mount + content[pos:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply to the existing runtime and restart it")
    args = parser.parse_args()
    destination, digest = prepare_overlay()
    if args.apply:
        code = "import hashlib;from pathlib import Path;print(hashlib.sha256(Path(" + repr(TARGET) + ").read_bytes()).hexdigest())"
        actual = subprocess.check_output(["docker", "exec", "nexent-runtime", "python", "-c", code], text=True, timeout=10).strip()
        if actual not in (BASE_SHA256, digest):
            raise ValueError("Running SDK module differs from both the pinned base and approved overlay")
        if actual != digest:
            subprocess.run(["docker", "cp", str(destination), "nexent-runtime:" + TARGET], check=True, capture_output=True, timeout=20)
            subprocess.run(["docker", "restart", "nexent-runtime"], check=True, capture_output=True, timeout=60)
        verified = subprocess.check_output(["docker", "exec", "nexent-runtime", "python", "-c", code], text=True, timeout=10).strip()
        if verified != digest:
            raise RuntimeError("Runtime overlay verification failed")
        record = {"base_sdk_sha256": BASE_SHA256, "overlay_sha256": digest, "runtime_verified": True,
                  "scope": "Blank MCP connection failures only; existing routes, success path and cleanup are preserved",
                  "target": TARGET, "source": "scripts/runtime_error_overlay.py"}
        (ROOT / "evidence/runtime-error-overlay.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("SDK connection error overlay prepared:", digest)


if __name__ == "__main__":
    main()

"""Prepare private native activity from an existing matter lifecycle; no model calls."""
import argparse
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle
from gov_mcp.matter_native import SERVICE, SCENARIO, FACTS, ACTOR, native_profile
from private_runtime import ensure_private_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--protocol", choices=("legacy", "batched-close-v2"), default="legacy")
    args = parser.parse_args()
    if args.runtime.exists() and any(args.runtime.iterdir()):
        parser.error("Existing native activity preserved")
    if not (args.lifecycle / "matter-lifecycle.json").is_file():
        parser.error("Existing source-bound lifecycle required")
    lifecycle = MatterLifecycle(args.lifecycle)
    result = lifecycle.query(FACTS, principal=ACTOR)
    if result.get("status") != "DRAFT_PROOF_READY":
        parser.error("Current evolved source-bound proof required")
    ensure_private_directory(args.runtime)
    profile = native_profile(args.protocol)
    manifest = profile["manifest"]
    access = {"lifecycle_root": str(args.lifecycle.resolve()), "mcp_token": secrets.token_urlsafe(32)}
    for name, value in (("manifest.json", manifest), ("access.json", access)):
        (args.runtime / name).write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"prepared": True, "protocol": args.protocol, "native_run_limit": profile["run_limit"],
        "model_calls": 0, "knowledge_published": False}))


if __name__ == "__main__": main()

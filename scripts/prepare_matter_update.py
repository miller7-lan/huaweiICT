"""Prepare a controlled three-source baseline and Nexent-auth web runtime. No HTTP/model calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_lifecycle import MatterLifecycle, Principal
from gov_mcp.matter_update import MatterUpdater, build_snapshot, inspect_capture, encoded
from private_runtime import ensure_private_directory

TEMPLATE = ROOT / "data/real-matter-closure/nanjing/review-pack-v1"
LOCAL = ["NJ-POLICE-FAQ-2025", "NJ-CYCLE-2026", "NJ-OLD-CARD-2025"]
ACTOR = Principal("source-update-engineering-host", "maintainer", "service")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--role-map", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, help="可选：用新活动实际采集的回执初始化，过期归档不能改时间戳代替")
    args = parser.parse_args()
    if args.runtime.exists() and any(args.runtime.iterdir()): parser.error("Existing update activity preserved")
    if args.snapshots.exists() and any(args.snapshots.iterdir()): parser.error("Existing snapshots preserved")
    roles = json.loads(args.role_map.read_text(encoding="utf-8"))
    if not roles: parser.error("Trusted existing Nexent role map required")
    if args.capture_root and any(row["kind"] not in ("unchanged", "raw_only_changed")
            for row in inspect_capture(TEMPLATE, args.capture_root, LOCAL)):
        parser.error("Observed source body/metadata changed; re-review before creating a baseline")
    ensure_private_directory(args.runtime)
    build_snapshot(TEMPLATE, args.snapshots / "registered-three", LOCAL, capture_root=args.capture_root)
    service = MatterLifecycle(args.runtime / "service")
    updater = MatterUpdater(args.runtime, service)
    staged = updater.stage(args.snapshots / "registered-three", ontology_stage="seed", expected_head=None, principal=ACTOR)
    released = updater.release_engineering(staged["update_id"], note="Controlled initial registration of three actual archived local documents; not historical website state or business approval", principal=ACTOR)
    for name, value in (("web-settings.json", {"schema": 1, "auth_mode": "nexent_accounts", "nexent_url": "http://127.0.0.1:5010", "formal_business_publication": False}),
        ("nexent-roles.json", roles), ("update-preparation.json", {"schema": 1, "baseline_version": released["version"],
            "snapshot_root": str(args.snapshots.resolve()), "baseline_sources": LOCAL, "model_calls": 0, "knowledge_published": False})):
        (args.runtime / name).write_bytes(encoded(value))
    print(json.dumps({"prepared": True, "baseline_sources": 3, "baseline_version": released["version"], "new_http_requests": 0,
        "model_calls": 0, "knowledge_published": False}))


if __name__ == "__main__": main()

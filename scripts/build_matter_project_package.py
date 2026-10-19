"""Package a committed public project snapshot, excluding only redundant deliveries."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "deliveries/nanjing-project-v1")
    args = parser.parse_args()
    destination = args.destination.resolve(); archive_path = destination.with_suffix(".zip")
    if destination.exists() or archive_path.exists(): parser.error("Existing sealed delivery preserved")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT).returncode:
        parser.error("Commit tracked changes before packaging")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(["git", "archive", "--format=zip", commit], cwd=ROOT)
    excluded, files = [], {}
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        for item in archive.infolist():
            name = item.filename
            if item.is_dir(): continue
            if name.startswith("deliveries/") or name.startswith("data/real-matter-closure/nanjing/update-input-package-v1"):
                excluded.append(name); continue
            if any(part in {".runtime", ".venv", ".git", "__pycache__"} for part in Path(name).parts) or Path(name).name.startswith(".env") and Path(name).name != ".env.example":
                raise ValueError("Private path found in tracked snapshot")
            files[name] = archive.read(item)
    required = ("scripts/reproduce_matter_project.py", "scripts/verify_matter_update_inputs.py", "scripts/provision_matter_native.py",
        "config/skills/govmatter-source-proof-first/SKILL.md", "docs/南京事项项目交付与复现.md")
    if not all(name in files for name in required): raise ValueError("Committed delivery entrypoints required")
    # Exact matches against local credentials; never export their values or hashes.
    secrets = set()
    def collect(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items(): collect(v, k)
        elif isinstance(value, list):
            for v in value: collect(v, key)
        elif isinstance(value, str) and len(value) >= 12 and any(k in key.lower() for k in ("password", "token", "api_key")):
            secrets.add(value.encode())
    local = ROOT / ".runtime"
    for path in [local / "accounts.json", *(local / "real-matter-closure").glob("*/access.json")]:
        if path.is_file(): collect(json.loads(path.read_text(encoding="utf-8-sig")))
    if any(secret in raw for secret in secrets for raw in files.values()):
        raise ValueError("Known local credential matched; nothing exported")
    files["DELIVERY.md"] = files["docs/南京事项项目交付与复现.md"].replace(
        "(南京事项本地原生编排.md)".encode(), "(docs/南京事项本地原生编排.md)".encode())
    manifest = {"schema": 1, "source_commit": commit, "scope": "public_project_snapshot_without_redundant_distribution_copies",
        "excluded_redundant_paths": sorted(excluded), "known_local_credential_matches": 0,
        "known_local_credential_scan_available": bool(secrets), "human_business_review": False, "formal_publication": False,
        "files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())]}
    files["delivery-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    for name, raw in sorted(files.items()):
        path = (destination / name).resolve()
        if not path.is_relative_to(destination): raise ValueError("Delivery path escaped destination")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output: output.write(raw)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            item = zipfile.ZipInfo(destination.name + "/" + name, date_time=(2026, 10, 19, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(item, raw)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None: raise ValueError("Delivery ZIP damaged")
    print(json.dumps({"source_commit": commit, "files": len(files), "zip": str(archive_path),
        "zip_bytes": archive_path.stat().st_size, "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "known_local_credential_matches": 0, "model_calls": 0}))


if __name__ == "__main__": main()

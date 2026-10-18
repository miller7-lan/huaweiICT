"""Complete data bundles: online SQLite snapshot, referenced originals and non-auth configuration."""

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from gov_mcp.knowledge import KnowledgeStore, encode


CONFIG_FILES = {"phase.json", "scope-regions.json", "knowledge-scope.json", "publication-policy.json", "sources.json"}
MAX_FILES, MAX_TOTAL_BYTES = 100000, 2 * 1024**3


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_path(root, name):
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise ValueError("Invalid bundle file path")
    parts = PurePosixPath(name)
    if parts.is_absolute() or any(p in (".", "..") for p in parts.parts) or not parts.parts or str(parts) != name:
        raise ValueError("Invalid bundle file path")
    if name not in CONFIG_FILES | {"knowledge.sqlite3"} and not re.fullmatch(r"originals/([0-9a-f]{2})/([0-9a-f]{64})", name):
        raise ValueError("Unrecognized bundle file")
    path = root.joinpath(*parts.parts)
    for candidate in (path, *path.parents):
        if candidate == root.parent:
            break
        if candidate.is_symlink():
            raise ValueError("Symlinks are not accepted in a bundle")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundle path escapes root")
    return path


def database_manifest(path):
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Snapshot database integrity check failed")
        originals = [row[0] for row in db.execute("SELECT DISTINCT sha256 FROM revisions ORDER BY sha256")]
        if any(not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha) for sha in originals):
            raise ValueError("Invalid original fingerprint in snapshot")
        sources = [json.loads(r[0]) for r in db.execute("SELECT config FROM sites ORDER BY id")]
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("documents", "revisions", "chunks", "versions", "heads")}
    return originals, sources, counts


def create_bundle(store, target):
    target = Path(target).resolve()
    if target.exists() or target == store.root or target.is_relative_to(store.root) and target.relative_to(store.root).parts[0] != "backups":
        raise ValueError("Use a new backup directory, outside live data or beneath backups/")
    target.mkdir(parents=True, exist_ok=False)
    config_before = {name: (store.root / name).read_bytes() for name in CONFIG_FILES - {"sources.json"} if (store.root / name).is_file()}
    database = target / "knowledge.sqlite3"
    with store.db() as source, sqlite3.connect(database) as destination:
        source.backup(destination)
    originals, sources, counts = database_manifest(database)
    files = ["knowledge.sqlite3"]
    for name in sorted(config_before):
        source = store.root / name
        if source.is_file():
            if source.is_symlink():
                raise ValueError("Configuration symlinks cannot be backed up")
            (target / name).write_bytes(config_before[name])
            files.append(name)
    if not {"phase.json", "publication-policy.json"} <= set(files):
        raise ValueError("Runtime marker and publication policy are required")
    (target / "sources.json").write_text(encode({"sources": sources}), encoding="utf-8")
    files.append("sources.json")
    for sha in originals:
        name = "originals/"+sha[:2]+"/"+sha
        source = checked_path(store.root, name)
        if file_digest(source) != sha:
            raise ValueError("Live original fingerprint mismatch")
        destination = checked_path(target, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if file_digest(destination) != sha:
            raise ValueError("Copied original fingerprint mismatch")
        files.append(name)
    inventory = {name: {"sha256": file_digest(checked_path(target, name)), "size": checked_path(target, name).stat().st_size} for name in sorted(files)}
    config_after = {name: (store.root / name).read_bytes() for name in CONFIG_FILES - {"sources.json"} if (store.root / name).is_file()}
    if config_before != config_after:
        raise ValueError("Configuration changed during backup; retry into another new directory")
    manifest = {"schema": 1, "kind": "knowledge_data_backup", "complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": inventory, "counts": counts,
        "referenced_originals": len(originals), "auth_credentials_included": False,
        "excluded": "MCP tokens, identity mappings, accounts, logs, process state, caches and unreferenced temporary originals",
        "restore_requirement": "Restore into a new isolated directory. Re-provision private service credentials separately."}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(target), "manifest_sha256": file_digest(target / "manifest.json"),
            "files": len(files), "referenced_originals": len(originals), "counts": counts, "auth_credentials_included": False}


def restore_bundle(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Restore target must be a new isolated directory")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("kind") != "knowledge_data_backup" or manifest.get("complete") is not True:
        raise ValueError("Completed recognized backup required")
    inventory = manifest.get("files")
    if not isinstance(inventory, dict) or not {"phase.json", "knowledge.sqlite3", "publication-policy.json", "sources.json"} <= inventory.keys() or len(inventory) > MAX_FILES:
        raise ValueError("Incomplete or oversized bundle inventory")
    total = 0
    for name, expected in inventory.items():
        path = checked_path(source, name)
        if not isinstance(expected, dict) or type(expected.get("size")) is not int or expected["size"] < 0:
            raise ValueError("Invalid bundle file size")
        total += expected["size"]
        if total > MAX_TOTAL_BYTES or not path.is_file() or path.stat().st_size != expected["size"] or file_digest(path) != expected.get("sha256"):
            raise ValueError("Missing, changed or oversized bundle file")
    originals, _, counts = database_manifest(source / "knowledge.sqlite3")
    if any("originals/"+sha[:2]+"/"+sha not in inventory for sha in originals):
        raise ValueError("Bundle omits a referenced original")
    if any(file_digest(checked_path(source, "originals/"+sha[:2]+"/"+sha)) != sha for sha in originals):
        raise ValueError("Original content does not match the database fingerprint")
    marker = json.loads((source / "phase.json").read_text(encoding="utf-8"))
    if marker != {"phase": "knowledge_update", "schema_version": 1}:
        raise ValueError("Dedicated knowledge update marker required")
    snapshot = KnowledgeStore(source, read_only=True)
    with snapshot.db() as db:
        for revision in db.execute("SELECT id FROM revisions"):
            snapshot._verify(db, revision[0], require_review=False)
        for version in db.execute("SELECT members FROM versions WHERE state='published'"):
            for member in json.loads(version[0]):
                snapshot._verify(db, member)
    target.mkdir(parents=True, exist_ok=False)
    # The marker is copied last so a partially restored directory cannot be mistaken for ready.
    for name in sorted(inventory, key=lambda value: value == "phase.json"):
        original, restored = checked_path(source, name), checked_path(target, name)
        restored.parent.mkdir(parents=True, exist_ok=True)
        with original.open("rb") as input_stream, restored.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
        if file_digest(restored) != inventory[name]["sha256"]:
            raise ValueError("Restore copy fingerprint mismatch")
    restored_store = KnowledgeStore(target)
    # Never advertise backed-up workers as currently alive. This does not start any process.
    with restored_store.db() as db:
        db.execute("UPDATE workers SET progress='completed'")
        restored_store.log(db, "data_bundle_restored", {"manifest_sha256": file_digest(source / "manifest.json")})
    result = {"restored_to": str(target), "counts": counts, "referenced_originals": len(originals),
              "services_started": False, "credentials_must_be_reprovisioned": True,
              "source_manifest_sha256": file_digest(source / "manifest.json")}
    (target / "restore-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

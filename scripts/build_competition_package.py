"""Build/verify an offline allowlisted engineering handoff; never read private runtime.

This package is not a native Agent export or a completed business acceptance.
Only Python's standard library is needed. No network/model/deployment is invoked.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/competition-20261021"
ARCHIVE = "ict-topic3-public-engineering-20261021.zip"
GUIDE = "docs/competition/复现与交付说明-2026-10-21.md"
# Each directory is an explicit code/config allowlist, with exact suffix limits.
# Original documents, evidence, data catalogs and vendor dependencies are omitted.
TREE_ALLOWLIST = {
    "src/gov_mcp": {".py", ".html", ".css", ".js", ".ps1"},
    "config/skills": {".md"},
    "config/decision": {".json"},
    "config/preparation": {".json", ".txt", ".lock"},
    "config/knowledge": {".json"},
}
FILE_ALLOWLIST = (
    "AGENTS.md", "pyproject.toml", "requirements.lock", "requirements-knowledge.lock",
    ".dockerignore", ".gitignore", ".gitattributes", "Dockerfile", "Dockerfile.knowledge",
    "compose.mcp.yml", "compose.knowledge.yml", "compose.cloud-proxy.yml",
    "compose.m1.yml", "compose.m2.yml", "compose.asset-query.yml",
    "deploy/minio-source.Dockerfile", GUIDE,
    "docs/competition/当前开发设计正文-2026-10-21.md",
    "docs/competition/当前Nexent配置与证据说明-2026-10-21.md",
    "docs/competition/真实资产与规范对齐表-2026-10-21.md",
    "docs/competition/独立评测执行方案-2026-10-21.md",
    "config/knowledge-expansion.json", "config/knowledge-scope.json", "config/m2-data-taxonomy.json",
    "scripts/build_competition_package.py", "scripts/setup.ps1", "scripts/deploy.ps1",
    "scripts/private_runtime.py", "scripts/prepare_runtime.py", "scripts/prepare_deploy.py",
    "scripts/runtime_error_overlay.py", "scripts/runtime_sandbox.py", "scripts/check_docker.py",
    "scripts/check_native_ready.py", "scripts/build_minio.ps1", "scripts/record_minio_build.py",
    "scripts/configure_ollama.ps1", "scripts/download_models.py", "models/Modelfile.4b", "models/Modelfile.8b",
    "scripts/nexent_api.py", "scripts/technical_controls.py", "scripts/initialize_accounts.ps1",
    "scripts/initialize_super_admin.sh", "scripts/provision_nexent.py", "scripts/build_agents.py",
    "scripts/prepare_crossdoc_sources.py", "scripts/run_new_knowledge_experiment.py",
    "scripts/run_ontology_evolution.py", "scripts/run_ontology_crossdoc.py",
    "scripts/prepare_ontology_crossdoc_native.py", "scripts/provision_ontology_crossdoc_native.py",
    "scripts/verify_ontology_crossdoc_native.py",
    "scripts/reassess_ontology_crossdoc_native.py",
    "scripts/export_ontology_crossdoc_native.py",
    "config/exports/ontology-crossdoc-native-20261021/agent.json",
    "evidence/ontology-crossdoc-native-20261021/actual-export.json",
    "evidence/ontology-crossdoc-native-20261021/summary.json",
    "docs/赛题3资料/南京本体原生统一验收-2026-10-21.md",
    "evidence/ontology-crossdoc-native-20261021/20261021T133609700710Z.json",
    "evidence/ontology-crossdoc-native-20261021/offline-event-contract-correction.json",
    "evidence/ontology-crossdoc-native-20261021/native-authorization.json",
    "evidence/ontology-crossdoc-native-20261021/protection-extension.json",
    "evidence/ontology-crossdoc-native-20261021/bridge-checks-final.xml",
    "evidence/ontology-crossdoc-native-20261021/runner-event-contract-checks.xml",
    "scripts/provision_crossdoc_native_preview.py", "scripts/verify_crossdoc_native_preview.py",
    "scripts/check_successor_autonomy_cloud.py", "scripts/provision_agent_workflow.py",
    "scripts/start_agent_workflow.ps1", "scripts/prepare_knowledge.py", "scripts/provision_knowledge.py",
    "scripts/configure_deepseek.py", "scripts/verify_workflow_connections.py",
    "tests/test_crossdoc_reasoning.py", "tests/test_crossdoc_preview.py",
    "tests/test_ontology_seed.py", "tests/test_ontology_evolution.py", "tests/test_ontology_public_source.py",
    "tests/test_ontology_crossdoc.py", "tests/test_crossdoc_native_wiring.py", "tests/test_successor_update.py",
    "tests/test_ontology_crossdoc_preview.py", "tests/test_ontology_crossdoc_provision.py",
    "evidence/ontology-crossdoc-20261020/demonstration-20261021T111607928497Z.json",
    "evidence/ontology-crossdoc-20261020/structural-confirmation-20261021.json",
)
OPTIONAL_FILE_ALLOWLIST = (
    "scripts/run_henan_multimodal.py",
    "tests/test_henan_multimodal.py",
    "tests/test_henan_multimodal_sources.py",
    "docs/赛题3资料/河南三模态归档候选-2026-10-22.md",
    "evidence/henan-multimodal-20261022/candidate.json",
    "evidence/henan-multimodal-20261022/summary.json",
    "tests/test_ontology_crossdoc_runner.py",
    "scripts/prepare_competition_multimodal.py",
    "evidence/competition-multimodal-20261021/preparation.json",
    "evidence/competition-multimodal-20261021/local-extraction.json",
    "evidence/competition-multimodal-20261021/scan-visual-observation.json",
)
SENSITIVE_KEYS = re.compile(
    r"^(password|passwd|api_key|access_token|refresh_token|authorization_token|mcp_token|client_secret|secret_key|token)$",
    re.I,
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_-]{28,}"),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"),
)
META_NAMES = {"manifest.json", "SHA256SUMS.txt", "source-requirements.json", "PACKAGE-NOTICE.md"}
MODEL_RECIPES = {"models/Modelfile.4b", "models/Modelfile.8b"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("NONFINITE_JSON_VALUE")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)


def safe_name(name: str) -> None:
    parts = PurePosixPath(name).parts
    if (not name or "\\" in name or ":" in name or name.startswith("/") or ".." in parts
            or any(p.lower() in {".runtime", ".git", ".venv", "vendor", "__pycache__"} for p in parts)
            or ("models" in (p.lower() for p in parts) and name not in MODEL_RECIPES)
            or any(p.lower().startswith(".env") for p in parts)
            or PurePosixPath(name).suffix.lower() in {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".p12"}):
        raise ValueError("FORBIDDEN_PACKAGE_PATH: " + name)


def checked_file(name: str) -> Path:
    safe_name(name)
    path = ROOT / name
    if not path.is_file() or not path.resolve().is_relative_to(ROOT):
        raise ValueError("MISSING_OR_EXTERNAL_ALLOWLIST_FILE: " + name)
    for node in (path, *path.parents):
        if node == ROOT.parent:
            break
        if node.is_symlink() or getattr(node.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("REPARSE_POINT_NOT_ALLOWED: " + name)
    return path


def inspect_content(name: str, raw: bytes) -> None:
    text = raw.decode("utf-8-sig")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        # Deliberately never print the matching credential-like string.
        raise ValueError("POSSIBLE_SECRET_REQUIRES_REVIEW: " + name)
    if name.endswith(".json"):
        value = strict_json(text)

        def walk(obj):
            if isinstance(obj, dict):
                for key, item in obj.items():
                    if SENSITIVE_KEYS.fullmatch(key) and item not in (None, "", False):
                        raise ValueError("NONEMPTY_SECRET_FIELD: " + name + ":" + key)
                    if key == "credential_ref" and item is not None and not (
                            isinstance(item, str) and re.fullmatch(r"env:[A-Z][A-Z0-9_]*", item)):
                        raise ValueError("NON_ENV_CREDENTIAL_REFERENCE: " + name)
                    walk(item)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(value)
        if name == "config/decision/connections.json" and (
                value.get("inference_enabled") is not False or value.get("form_verifier_enabled") is not False):
            raise ValueError("CONNECTIONS_MUST_REMAIN_DISABLED")


def selected_names() -> list[str]:
    names = set(FILE_ALLOWLIST)
    names.update(name for name in OPTIONAL_FILE_ALLOWLIST if (ROOT / name).is_file())
    for directory, suffixes in TREE_ALLOWLIST.items():
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "config").glob("agents.*.json"))
    return sorted(names)


def source_requirements() -> dict:
    # Read constants as data. Importing application modules is unnecessary.
    tree = ast.parse(checked_file("src/gov_mcp/crossdoc_reasoning.py").read_text("utf-8"))
    identity = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SOURCE_IDENTITY" for t in node.targets))
    entries = [{"chain": "nanjing_crossdoc", "role": role, "source_id": item["source_id"],
                "url": item["url"], "expected_original_sha256": item["raw_sha256"]}
               for role, item in identity.items()]
    pair_path = checked_file("data/knowledge/update-test-pairs/henan-id-time.json")
    pair = strict_json(pair_path.read_text("utf-8"))
    entries.extend({"chain": "henan_successor_update", "role": item["role"], "source_id": item["source_id"],
                    "url": item["url"], "expected_original_sha256": item["original_sha256"]}
                   for item in pair["sources"])
    multimodal_script = ROOT / "scripts/prepare_competition_multimodal.py"
    if multimodal_script.is_file():
        tree = ast.parse(multimodal_script.read_text("utf-8"))
        specs = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "SPECS" for t in node.targets))
        entries.extend({"chain": "henan_multimodal_preparation", "role": value[2], "source_id": source,
                        "url": value[0], "expected_original_sha256": value[1]} for source, value in specs.items())
    for entry in entries:
        entry.update(original_included=False, redistribution_permission="not_verified",
                     current_applicability="unknown", formal_approval=False,
                     acquisition="Obtain lawfully from the cited official URL or an authorized custodian; preserve retrieval receipt and exact original bytes. Stop on a hash mismatch; do not substitute a PDF print or renew an old approval.")
    return {"schema_version": 1, "source_originals_included": 0,
            "input_metadata_sha256": {"data/knowledge/update-test-pairs/henan-id-time.json": sha(pair_path.read_bytes())},
            "sources": entries,
            "required_preparation_metadata": [
                "data/knowledge/candidate-catalog.json (not included: contains extracted source text; distribution review pending)",
                "data/knowledge/expansion-batches/*.json (not included: obtain the reviewed matching source specifications)",
                "Verified source acquisition receipts, parsed locators, full clauses and private source bundle manifests",
                "Henan pair-definition.json and matching reviewed provenance, prepared in a new private workspace",
            ]}


def package_notice() -> bytes:
    return ("# Public engineering handoff, 2026-10-21\n\n"
            "This archive contains allowlisted project code, configuration templates, Skills and synthetic contract tests. "
            "No original government document, dependency binary, private runtime, database, credential, budget ledger, native conversation or raw trace is included.\n\n"
            "Read docs/competition/复现与交付说明-2026-10-21.md first. Other current materials may link historical evidence not included in this package; obtain the matching reviewed repository records separately. Source originals and exact preparation metadata require separate authorized acquisition. "
            "The actual three-agent reviewed-ontology tree JSON is included; the platform's Skill-bundled ZIP is a separate checked deliverable. A clean-environment native reproduction is outstanding. "
            "Synthetic engineering checks do not establish current policy validity or business quality.\n\n"
            "No project-wide redistribution license is declared by this package. Confirm author permission before external release. "
            "Third-party dependencies and official Nexent sources must be obtained under their own licenses; none are vendored here.\n").encode("utf-8")


def verify(archive: Path, manifest_path: Path) -> dict:
    archive_hash = sha(archive.read_bytes())
    checksum = (OUTPUT / "PACKAGE-SHA256.txt").read_text("ascii")
    if checksum != f"{archive_hash}  {archive.name}\n":
        raise ValueError("ARCHIVE_CHECKSUM_MISMATCH")
    external = manifest_path.read_bytes()
    manifest = strict_json(external.decode("utf-8"))
    entries = manifest["files"]
    expected = {entry["path"]: entry for entry in entries}
    if len(expected) != len(entries):
        raise ValueError("DUPLICATE_MANIFEST_ENTRY")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | META_NAMES:
            raise ValueError("ZIP_MEMBERS_DIFFER_FROM_ALLOWLIST")
        if bundle.read("manifest.json") != external:
            raise ValueError("INNER_EXTERNAL_MANIFEST_DIFFER")
        for name in names:
            safe_name(name)
            info = bundle.getinfo(name)
            if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                raise ValueError("ZIP_SYMLINK_NOT_ALLOWED")
            raw = bundle.read(name)
            inspect_content(name, raw)
            if name in expected and (sha(raw) != expected[name]["sha256"] or len(raw) != expected[name]["bytes"]):
                raise ValueError("ZIP_PAYLOAD_HASH_MISMATCH: " + name)
        for name in ("source-requirements.json", "PACKAGE-NOTICE.md", "SHA256SUMS.txt"):
            raw = bundle.read(name)
            if sha(raw) != manifest["metadata_sha256"][name] or (OUTPUT / name).read_bytes() != raw:
                raise ValueError("ZIP_METADATA_HASH_MISMATCH: " + name)
        wanted_checksums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
        if bundle.read("SHA256SUMS.txt").decode("utf-8") != wanted_checksums:
            raise ValueError("PAYLOAD_CHECKSUM_LIST_MISMATCH")
        if bundle.testzip() is not None:
            raise ValueError("ZIP_CRC_FAILURE")
    return {"status": "PASS_PACKAGE_INTEGRITY_ONLY", "payload_files": len(entries), "zip_members": len(names),
            "original_source_files": 0, "network_calls": 0, "model_calls": 0,
            "business_acceptance": False, "clean_native_reproduction": False,
            "archive_sha256": archive_hash}


def build() -> dict:
    files = {}
    for name in selected_names():
        raw = checked_file(name).read_bytes()
        inspect_content(name, raw)
        files[name] = raw
    sources = encoded(source_requirements())
    checksums = "".join(f"{sha(raw)}  {name}\n" for name, raw in files.items()).encode("utf-8")
    meta = {"source-requirements.json": sources, "PACKAGE-NOTICE.md": package_notice(), "SHA256SUMS.txt": checksums}
    manifest = {
        "schema_version": 1, "package_kind": "allowlisted_public_engineering_handoff",
        "generated_at": datetime.now(timezone.utc).isoformat(), "archive": ARCHIVE,
        "business_acceptance_complete": False, "clean_environment_native_reproduction_complete": False,
        "original_source_files_included": 0, "runtime_files_read_or_copied": 0,
        "network_calls": 0, "model_calls": 0, "existing_budgets_changed": False,
        "scope": "Current allowlisted working-tree bytes, not a Git commit release or self-contained native export.",
        "allowlist": {"trees_and_suffixes": {key: sorted(value) for key, value in TREE_ALLOWLIST.items()},
                      "files": list(FILE_ALLOWLIST), "optional_files": list(OPTIONAL_FILE_ALLOWLIST),
                      "additional_root_config_glob": "config/agents.*.json"},
        "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)} for name, raw in files.items()],
        "metadata_sha256": {name: sha(raw) for name, raw in meta.items()},
        "connection_safety": {"plaintext_credentials_in_json": False, "credential_references": "env:VARIABLE_NAME only",
                              "inference_enabled": False, "form_verifier_enabled": False},
        "excluded": [".runtime", ".env*", "databases", "private raw logs", "personal answers", "budget ledgers",
                     "government original archives", "unreviewed extracted source catalogs", "vendor", "model weights and unlisted model files", "unlisted evidence", "build", "output"],
        "must_obtain_separately": [
            {"kind": "data", "requirement": "Official original sources, exact hashes and matching preparation metadata listed in source-requirements.json; seven sources across the native and multimodal preparation scopes. Originals are separately held, not included."},
            {"kind": "credentials", "requirement": "New private Nexent accounts, tenant/group/role bindings, MCP token and approved environment-based API credentials; never copy another machine's accounts or tokens."},
            {"kind": "human_confirmation", "requirement": "Human review of the exact ontology candidate ID and expected head, review identity, explicit activation/rollback confirmation; source applicability and publication remain separate gates."},
            {"kind": "native_exports", "requirement": "The actual new reviewed-ontology three-agent tree is config/exports/ontology-crossdoc-native-20261021/agent.json; its unmodified Skill-bundled native ZIP is supplied separately. Rebind models, tools, Skills, groups and credentials on receipt. Historical crossdoc/successor exports require separate retrieval if those additional flows will be shown."},
            {"kind": "legacy_runtime_dependencies", "requirement": "Current provision scripts require protected prior native drafts and preparation manifests; a fresh tenant needs an approved standalone migration/import path, not fabricated IDs or copied budget state."},
            {"kind": "platform", "requirement": "Python 3.11+ (reference Windows 3.12), Git, Docker Desktop/WSL2, official Nexent v2.7.0 commit 519b9b17c7f4cbc623256ac318ea96a20f33772e, separately obtained dependencies/models."},
            {"kind": "acceptance", "requirement": "Another member must record clean-environment import and end-to-end native execution after obtaining the missing inputs; independent business evaluation remains separate."},
            {"kind": "document_evidence_links", "requirement": "The four current Markdown materials link to historical evidence/requirements outside this allowlist; obtain the matching reviewed repository records separately. No private runtime or raw logs are part of those attachments."},
            {"kind": "redistribution", "requirement": "Confirm project author permission and third-party document/data licenses before external submission or public redistribution."},
        ],
    }
    external = encoded(manifest)
    # No cleanup of any source/runtime directory. Replace only these known deliverables.
    if not OUTPUT.resolve().is_relative_to(ROOT):
        raise ValueError("UNSAFE_OUTPUT_DIRECTORY")
    for node in (OUTPUT, *OUTPUT.parents):
        if node == ROOT.parent:
            break
        if node.exists() and (node.is_symlink() or getattr(node.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("OUTPUT_REPARSE_POINT_NOT_ALLOWED")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    targets = (ARCHIVE, "manifest.json", "SHA256SUMS.txt", "source-requirements.json", "PACKAGE-NOTICE.md", "PACKAGE-SHA256.txt", "verification.json")
    if any(path.exists() and (path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
           for path in (OUTPUT / name for name in targets)):
        raise ValueError("OUTPUT_SYMLINK_NOT_ALLOWED")
    with zipfile.ZipFile(OUTPUT / ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, raw in sorted({**files, **meta, "manifest.json": external}.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, raw)
    for name, raw in {**meta, "manifest.json": external}.items():
        (OUTPUT / name).write_bytes(raw)
    (OUTPUT / "PACKAGE-SHA256.txt").write_text(f"{sha((OUTPUT / ARCHIVE).read_bytes())}  {ARCHIVE}\n", encoding="ascii")
    # A concurrent edit must not silently turn this into a mixed source snapshot.
    if any(checked_file(name).read_bytes() != raw for name, raw in files.items()):
        raise ValueError("SOURCE_CHANGED_DURING_BUILD_RERUN_AFTER_EDITS_SETTLE")
    report = verify(OUTPUT / ARCHIVE, OUTPUT / "manifest.json")
    (OUTPUT / "verification.json").write_bytes(encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Check existing ZIP/manifest without deployment or source execution")
    args = parser.parse_args()
    report = verify(OUTPUT / ARCHIVE, OUTPUT / "manifest.json") if args.verify_only else build()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

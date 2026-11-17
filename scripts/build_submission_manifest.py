"""Build the file list of the submission package, check it, and optionally write and re-check the zip.

  python scripts/build_submission_manifest.py --output .runtime/submission/manifest.json [--zip .runtime/submission/ICT-V31.zip] [--extra PATH ...]

What goes in:
  code      src/, scripts/, tests/, config/, deploy/, data/, evaluation/ and the top-level build files
  materials every file in --materials (the V3.1 update drafts), plus each repository file their
            Markdown links point to, followed one level further (a linked document's own links)
  extra     --extra paths, for example the merged Word and PDF once they exist

What stays out: team planning documents (task lists, work orders, handoff logs; listed in the
manifest as excluded_internal_planning), anything not tracked by Git, .runtime/, caches, and every name that marks private
data (records.jsonl, key.json, review sheets, access.json, raw SSE logs). A private-looking name
inside the selection makes the check fail rather than being dropped silently.

Checks (all must pass, exit 1 otherwise):
  - the eight core modules have exactly the SHA-256 recorded in the freeze receipt;
  - every included evidence JSON passes check_public_evidence.py;
  - no private-looking file is selected; every selected file is tracked by Git;
  - with --zip: the zip is reopened and each member's SHA-256 is compared with the manifest;
  - with --self-test: the zip is unpacked into an empty directory and CORE_TESTS must pass there.
    Run this before every upload: a package whose own tests cannot start is not submitted.
No model call and no network. The manifest lists path, bytes, SHA-256 and why each file is included.
"""
import argparse
import fnmatch
import hashlib
import json
import re
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_public_evidence import findings  # noqa: E402

CODE_DIRS = ("src", "scripts", "tests", "config", "deploy", "data", "evaluation")
TOP_FILES = ("README.md", "LICENSE", "pyproject.toml", "requirements.lock", "requirements-knowledge.lock",
             "Dockerfile", "Dockerfile.knowledge", "compose.asset-query.yml", "compose.cloud-proxy.yml",
             "compose.knowledge.yml", "compose.m1.yml", "compose.m2.yml", "compose.mcp.yml")
CORE_MODULES = ("frozen_qa.py", "frozen_retrieval.py", "frozen_qa_flow_v3.py", "frozen_qa_flow_server.py",
                "knowledge_retrieval.py", "claim_binding.py", "scope_gate_v2.py", "regional_scope.py")
PRIVATE = ("records.jsonl", "key.json", "sheet-*.csv", "access.json", "*-sse.log", "*.sse.log", "*.env",
           "*-report.json", "reference.json", "questions.json")
SKIP = ("*/__pycache__/*", "*.pyc", ".runtime/*")
# Files the packaged core tests read; synthetic or already public, always included.
TEST_SUPPORT = ("evaluation/refactor-bench-20261115/", "evidence/competition-multimodal-20261021/scan-visual-observation.json",
                "data/knowledge/candidate-catalog.json")
# The tests a reader can run inside the unpacked package (see --self-test). The full suite also reads
# development history that is not packaged, so it is not expected to pass outside the repository.
CORE_TESTS = ("tests/test_claim_binding.py", "tests/test_evaluation_tools_audit.py", "tests/test_regional_scope.py",
              "tests/test_frozen_qa_flow_v3.py", "tests/test_frozen_qa_flow_server.py", "tests/test_frozen_flow_native.py",
              "tests/test_flow_v3_review_pack.py", "tests/test_flow_v3_preflight.py",
              "tests/test_crossdoc_native_wiring.py", "tests/test_frozen_qa.py", "tests/test_frozen_retrieval.py",
              "tests/test_extract_fact_cards.py")
# Team planning and task lists are internal; they are listed as excluded, never packaged.
INTERNAL = ("*任务清单*", "*执行顺序*", "*下一批任务*", "*接续记录*", "*分工*", "*人工回交待办*")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return {name for name in out.decode("utf-8").split("\0") if name}


def links(markdown_path):
    """Repository files a Markdown document links to (relative links only)."""
    base = markdown_path.parent
    found = set()
    for target in LINK.findall(markdown_path.read_text("utf-8")):
        if "://" in target:
            continue
        path = (base / target).resolve()
        if path.is_relative_to(ROOT) and path.is_file():
            found.add(path.relative_to(ROOT).as_posix())
    return found


def select(materials, extra, git_files, exclude_dirs=()):
    chosen = {}
    for name in sorted(git_files):
        top = name.split("/")[0]
        if (top in CODE_DIRS and top not in exclude_dirs) or name in TOP_FILES:
            chosen[name] = "code"
        elif any(name == s or (s.endswith("/") and name.startswith(s)) for s in TEST_SUPPORT):
            chosen[name] = "test support"
    material_files = sorted(p.relative_to(ROOT).as_posix() for p in materials.rglob("*") if p.is_file())
    frontier = []
    for name in material_files:
        chosen[name] = "materials"
        if name.endswith(".md"):
            frontier.append(name)
    for depth in (1, 2):
        nxt = []
        for doc in frontier:
            for target in sorted(links(ROOT / doc)):
                if target not in chosen:
                    chosen[target] = f"linked from materials (level {depth})"
                    if target.endswith(".md"):
                        nxt.append(target)
        frontier = nxt
    for name in extra:
        chosen[Path(name).as_posix()] = "extra"
    chosen = {name: why for name, why in chosen.items() if not any(fnmatch.fnmatch(name, p) for p in SKIP)}
    internal = sorted(name for name, why in chosen.items()
                      if why != "code" and any(fnmatch.fnmatch(name.rsplit("/", 1)[-1], p) for p in INTERNAL))
    return {name: why for name, why in chosen.items() if name not in internal}, internal


def check(chosen, git_files, freeze):
    problems = []
    recorded = freeze["module_sha256"]
    for module in CORE_MODULES:
        actual = sha(ROOT / "src/gov_mcp" / module)
        if recorded.get(module) != actual:
            problems.append(f"MODULE_NOT_FROZEN: {module}")
    for name, why in chosen.items():
        base = name.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(base, p) for p in PRIVATE):
            problems.append(f"PRIVATE_NAME_SELECTED: {name}")
        if why != "extra" and name not in git_files:
            problems.append(f"NOT_TRACKED_BY_GIT: {name}")
        if name.startswith("evidence/") and name.endswith(".json"):
            try:
                found = findings(json.loads((ROOT / name).read_text("utf-8-sig")))
            except ValueError:
                found = ["$: not valid JSON"]
            if found:
                problems.append(f"EVIDENCE_NOT_PUBLIC: {name} ({len(found)} fields)")
    return problems


def write_zip(path, rows, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            archive.write(ROOT / row["path"], row["path"])
        archive.writestr("SUBMISSION-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    with zipfile.ZipFile(path) as archive:  # reopen and compare every member with the manifest
        expected = {row["path"]: row["sha256"] for row in rows}
        names = set(archive.namelist()) - {"SUBMISSION-MANIFEST.json"}
        mismatched = [n for n in names if hashlib.sha256(archive.read(n)).hexdigest() != expected.get(n)]
        missing = sorted(set(expected) - names)
    return {"zip": str(path), "zip_sha256": sha(path), "members": len(names),
            "members_mismatched": mismatched, "members_missing": missing}


def self_test(zip_path):
    """Unpack the zip into an empty directory and run CORE_TESTS there, with nothing from the repository."""
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(folder)
        subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        subprocess.run(["git", "add", "-A"], cwd=folder, check=True, capture_output=True)
        env = {**os.environ, "PYTHONPATH": str(Path(folder) / "src")}
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *CORE_TESTS],
                              cwd=folder, env=env, capture_output=True, text=True)
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
        failed = [line.split(" - ")[0] for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
        return {"command": "PYTHONPATH=src python -m pytest -q " + " ".join(CORE_TESTS),
                "exit_code": proc.returncode, "summary": tail, "failed": failed[:20], "passed": proc.returncode == 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--materials", type=Path, default=ROOT / "docs/competition/materials-v31-20261117")
    parser.add_argument("--freeze-receipt", type=Path,
                        default=ROOT / "evidence/crossdoc-flow-v3/V31-freeze-receipt-20261116.json")
    parser.add_argument("--extra", nargs="*", default=[], help="Further repository-relative files, e.g. the merged Word/PDF")
    parser.add_argument("--output", type=Path, required=True, help="New manifest JSON (outside the package)")
    parser.add_argument("--zip", type=Path, help="Also write the package zip and re-check every member")
    parser.add_argument("--exclude-dirs", nargs="*", default=[], choices=CODE_DIRS,
                        help="Code directories to leave out (test-support files are still included)")
    parser.add_argument("--self-test", action="store_true", help="With --zip: run the core tests inside the unpacked zip")
    args = parser.parse_args()
    if args.output.exists() or (args.zip and args.zip.exists()):
        parser.error("OUTPUT_EXISTS_KEEP_ORIGINAL")
    for name in args.extra:
        if not (ROOT / name).is_file():
            parser.error(f"EXTRA_NOT_FOUND: {name}")
    git_files = tracked()
    chosen, internal = select(args.materials.resolve(), args.extra, git_files, tuple(args.exclude_dirs))
    freeze = json.loads(args.freeze_receipt.read_text("utf-8"))
    problems = check(chosen, git_files, freeze)
    rows = [{"path": name, "bytes": (ROOT / name).stat().st_size, "sha256": sha(ROOT / name), "why": why}
            for name, why in sorted(chosen.items())]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {"scope": "submission package file list; no model call", "built_from_commit": head,
                "src_freeze_commit": freeze.get("src_freeze_commit") or "459e459ea8b4401d21d8a230ef2ca6f0f4be175c",
                "fingerprint": freeze.get("fingerprint"), "files": len(rows),
                "bytes": sum(row["bytes"] for row in rows),
                "by_reason": {why: sum(r["why"] == why for r in rows) for why in sorted({r["why"] for r in rows})},
                "excluded_internal_planning": internal, "excluded_code_dirs": list(args.exclude_dirs),
                "core_tests": list(CORE_TESTS),
                "problems": problems, "passed": not problems, "rows": rows}
    if args.zip and not problems:
        manifest["zip_check"] = write_zip(args.zip, rows, {k: v for k, v in manifest.items() if k != "zip_check"})
        zc = manifest["zip_check"]
        if zc["members_mismatched"] or zc["members_missing"]:
            manifest["passed"] = False
        if args.self_test:
            zc["self_test"] = self_test(args.zip)
            if not zc["self_test"]["passed"]:
                manifest["passed"] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "rows"}, ensure_ascii=False, indent=1))
    sys.exit(0 if manifest["passed"] else 1)


if __name__ == "__main__":
    main()

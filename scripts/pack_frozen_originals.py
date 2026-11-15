"""Pack or unpack the pinned originals of the frozen corpus for another machine (no network, no model).

pack    on the original machine: collect each source's original from the paths the corpus
        builder already trusts, check its SHA-256 against the catalogue, and write a ZIP
        whose members are named by that hash. The scanned PDF is left out: the corpus only
        uses its attributed transcription, which is in the repository.
unpack  on the other machine: check every member against the catalogue and place it where
        build_frozen_corpus.py looks first (.runtime/frozen-qa-20261030/originals/<sha256>).

The ZIP holds public archived documents, but it is passed privately like other runtime files.
"""
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_frozen_corpus as builder  # noqa: E402


def expected_originals():
    documents = builder.catalog_documents()
    return {documents[spec["source_id"]]["original_sha256"]: spec["source_id"]
            for spec in builder.SOURCES if spec["kind"] != "scan"}


def pack(output):
    documents, receipts, written = builder.catalog_documents(), [], []
    with ZipFile(output, "x", ZIP_DEFLATED) as archive:
        for spec in builder.SOURCES:
            if spec["kind"] == "scan":
                continue
            raw = builder.original(spec, documents[spec["source_id"]], None, receipts)
            digest = sha256(raw).hexdigest()
            archive.writestr(digest, raw)
            written.append({"source_id": spec["source_id"], "sha256": digest, "bytes": len(raw)})
    return written


def unpack(archive_path, target=None):
    target = Path(target or builder.RUNTIME / "originals")
    expected = expected_originals()
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        if sorted(names) != sorted(expected):
            raise ValueError("ARCHIVE_MEMBERS_DO_NOT_MATCH_CATALOGUE")
        target.mkdir(parents=True, exist_ok=True)
        placed = []
        for name in names:
            raw = archive.read(name)
            if sha256(raw).hexdigest() != name:
                raise ValueError("MEMBER_HASH_MISMATCH: " + expected[name])
            path = target / name
            if path.exists() and path.read_bytes() != raw:
                raise ValueError("DIFFERENT_FILE_ALREADY_PRESENT: " + name)
            path.write_bytes(raw)
            placed.append({"source_id": expected[name], "sha256": name, "bytes": len(raw)})
    return placed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pack").add_argument("--output", type=Path, required=True, help="New ZIP file")
    sub.add_parser("unpack").add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        result = pack(args.output) if args.command == "pack" else unpack(args.archive)
    except (RuntimeError, ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({args.command: result}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

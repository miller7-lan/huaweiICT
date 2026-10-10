"""Download pinned official Ollama blobs through the configured HTTP proxy.

This importer verifies registry digests and restricts redirects to the official
registry storage domain. It never disables Ollama's redirect protection.
"""

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REGISTRY = "https://registry.ollama.ai"
TAGS = ("4b-q4_K_M", "8b-q4_K_M")


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (
            host in ("registry.ollama.ai", "registry.ollama.com")
            or host.endswith(".r2.cloudflarestorage.com")
        ):
            raise RuntimeError("Registry redirect was outside the approved official storage domains")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_blob(opener, root, blob):
    algorithm, digest = blob["digest"].split(":", 1)
    if algorithm != "sha256" or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid registry digest")
    dest = root / "blobs" / f"sha256-{digest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == blob["size"] and sha256(dest) == digest:
        return
    partial = dest.with_suffix(".part")
    for attempt in range(12):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == blob["size"]:
            if sha256(partial) == digest:
                partial.replace(dest)
                return
            partial.unlink()
            offset = 0
        headers = {"User-Agent": "gov-blank-model-import/0.1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(f"{REGISTRY}/v2/library/qwen3/blobs/{blob['digest']}", headers=headers)
        try:
            with opener.open(request, timeout=30) as response:
                resumed = response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
                if response.status not in (200, 206) or (response.status == 206 and not resumed):
                    raise RuntimeError("Invalid registry range response")
                if not resumed:
                    offset = 0
                with partial.open("ab" if resumed else "wb") as target:
                    total = offset
                    announced = total // (128 * 1024 * 1024)
                    while chunk := response.read(4 * 1024 * 1024):
                        target.write(chunk)
                        total += len(chunk)
                        if total > blob["size"]:
                            raise RuntimeError("Blob exceeded its declared registry size")
                        progress = total // (128 * 1024 * 1024)
                        if progress > announced:
                            print(f"blob {digest[:12]}: {total / blob['size']:.0%}", flush=True)
                            announced = progress
            if partial.stat().st_size != blob["size"] or sha256(partial) != digest:
                raise RuntimeError("Blob integrity verification failed")
            partial.replace(dest)
            print(f"verified {digest[:12]} ({blob['size']} bytes)", flush=True)
            return
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            # Signed redirect URLs and proxy credentials must never enter logs.
            reason = type(getattr(exc, "reason", exc)).__name__
            print(f"blob {digest[:12]} attempt {attempt + 1}/12: {reason}", flush=True)
            if attempt == 11:
                raise RuntimeError(f"Download incomplete for blob {digest[:12]}; partial bytes retained") from None
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", choices=TAGS, action="append")
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models"))))
    parser.add_argument("--evidence", type=Path, default=Path("evidence/model-downloads.json"))
    args = parser.parse_args()
    opener = urllib.request.build_opener(OfficialRedirects())
    records = json.loads(args.evidence.read_text()) if args.evidence.exists() else {}
    for tag in args.tag or TAGS:
        url = f"{REGISTRY}/v2/library/qwen3/manifests/{tag}"
        for attempt in range(3):
            try:
                with opener.open(url, timeout=45) as response:
                    raw = response.read()
                    manifest_digest = response.headers.get("Docker-Content-Digest")
                break
            except OSError:
                if attempt == 2:
                    raise RuntimeError(f"Could not fetch official manifest for qwen3:{tag}") from None
        computed = "sha256:" + hashlib.sha256(raw).hexdigest()
        if manifest_digest and manifest_digest != computed:
            raise RuntimeError("Registry manifest digest mismatch")
        manifest = json.loads(raw)
        print(f"qwen3:{tag}: downloading verified official blobs", flush=True)
        for blob in [manifest["config"], *manifest["layers"]]:
            fetch_blob(opener, args.output, blob)
        dest = args.output / "manifests/registry.ollama.ai/library/qwen3" / tag
        dest.parent.mkdir(parents=True, exist_ok=True)
        pending = dest.with_suffix(".part")
        pending.write_bytes(raw)
        pending.replace(dest)
        records[f"qwen3:{tag}"] = {"manifest_digest": computed, "verified": True, "total_bytes": sum(x["size"] for x in [manifest["config"], *manifest["layers"]])}
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(records, indent=2) + "\n")
        print(f"Imported qwen3:{tag}; all blobs SHA-256 verified", flush=True)


if __name__ == "__main__":
    main()

"""Verify saved official originals and attempt one normal portal acquisition; no inference."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import secrets
import sys
from urllib.robotparser import RobotFileParser

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.public_preview import CASE, PAUSES, PORTAL, Sections, check_manifest, source_digest, utcnow, write, Preview
from private_runtime import ensure_private_directory
from run_public_business_pilot import checked_snapshot

RUNTIME = ROOT / ".runtime/public-native-preview"


def portal_capture(root):
    record = {"url": PORTAL, "status": "unavailable", "checked_at": utcnow(),
              "http_status": None, "raw_sha256": None, "method": "normal_public_http_no_login"}
    audit = []
    try:
        with httpx.Client(trust_env=True, timeout=30, follow_redirects=False,
                          headers={"User-Agent": "GovKB-Public-Preview/1.0"}) as client:
            robot_url = "https://www.hnzwfw.gov.cn/robots.txt"
            response = client.get(robot_url)
            audit.append({"stage": "robots", "http_status": response.status_code, "sha256": source_digest(response.content)})
            if response.status_code not in (200, 404):
                record.update(status="access_stopped", http_status=response.status_code)
                return record
            if response.status_code == 200:
                robots = RobotFileParser(robot_url); robots.parse(response.text.splitlines())
                if not robots.can_fetch("GovKB-Public-Preview/1.0", PORTAL):
                    record.update(status="access_stopped", http_status=200)
                    return record
            with client.stream("GET", PORTAL) as response:
                record["http_status"] = response.status_code
                if response.status_code != 200:
                    record["status"] = "access_stopped"
                    return record
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1024**2:
                        record["status"] = "unavailable"
                        return record
            (root / "sources/portal.html").write_bytes(raw)
            text = raw.decode("utf-8", errors="replace")
            visible = all(value in text for value in ("郑州市公安局二七分局", "申请材料", "承诺办理"))
            record.update(raw_sha256=source_digest(raw), status="captured_unreviewed" if visible else "http_shell")
            audit.append({"stage": "guide", "http_status": 200, "sha256": record["raw_sha256"], "bytes": len(raw)})
    except httpx.RequestError as error:
        audit.append({"stage": "transport", "error_type": type(error).__name__})
    finally:
        write(root / "portal-acquisition.json", {"checks": audit, "result": record})
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    args = parser.parse_args()
    root = RUNTIME
    ensure_private_directory(root)
    if (root / "preview.sqlite3").exists() and Preview(root).counts()["native_runs"]:
        parser.error("Existing campaign has runs; do not replace its pinned originals")
    old_root = ROOT / ".runtime/public-business/erqi-expiry"
    saved = checked_snapshot(json.loads((old_root / "snapshot.json").read_text("utf-8")))
    (root / "sources").mkdir(exist_ok=True)
    sources = []
    for source in saved["sources"]:
        (root / "sources" / (source["source_id"] + ".html")).write_bytes((old_root / (source["source_id"] + ".html")).read_bytes())
        sources.append({"source_id": source["source_id"], "url": source["url"], "data_class": "public",
            "raw_sha256": source["raw_sha256"], "retrieved_at": saved["retrieved_at"],
            "acquisition_method": "official_http_archive", "published_at": None, "effective_at": None,
            "sections": deepcopy(source["sections"])})
    manifest = {"schema": 1, "mode": "public_preview", "data_class": "public", "case": CASE,
                "sources": sources, "portal": portal_capture(root)}
    write(root / "manifest.json", manifest)
    write(root / "gates.json", {"mode": "public_preview", "paused_fields": PAUSES, "source_stopped": False})
    access = root / "access.json"
    if not access.exists():
        write(access, {"mcp_token": secrets.token_urlsafe(36)})
    check_manifest(root)
    print(json.dumps({"prepared": True, "official_archives": len(sources), "portal": manifest["portal"]["status"], "model_calls": 0}))


if __name__ == "__main__":
    main()

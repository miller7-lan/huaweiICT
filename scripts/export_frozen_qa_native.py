"""Export the two frozen-corpus Q&A drafts (literal MCP 38, knowledge base 39) as native Nexent JSON.

Each draft is exported unmodified through /agent/export, scanned for credentials
and checked to be the recorded disabled draft. agent.json goes to config/exports;
the raw ZIPs stay in ignored artifacts for the submission bundle. No model call.
"""
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_competition_package import inspect_content, sha  # noqa: E402
from nexent_api import NexentAPI  # noqa: E402
from provision_crossdoc_native_preview import read, write  # noqa: E402

RUNTIME = ROOT / ".runtime/frozen-qa-20261030"
DRAFTS = {"qa": (RUNTIME / "native-drafts.json", "govfrozenqa_assistant"),
          "kb": (RUNTIME / "kb/native-drafts.json", "govfrozenkb_assistant")}
PUBLIC = ROOT / "config/exports/frozen-qa-20261105"
ARCHIVE = ROOT / "artifacts/frozen-qa-exports-20261105"
OUTPUT = ROOT / "evidence/frozen-qa-20261030/native-export-20261105.json"


def export(api, agent_id, name):
    response = api.http.post(api.base + "/agent/export", json={"agent_id": agent_id},
                             headers={"Authorization": "Bearer " + api.token})
    response.raise_for_status()
    if len(response.content) > 5 * 1024 ** 2:
        raise ValueError("BOUNDED_NATIVE_EXPORT_REQUIRED")
    # A draft without Skills comes back as the JSON envelope rather than a ZIP; keep its data unmodified.
    exported = response.json()["data"]
    agent_bytes = json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8")
    inspect_content("agent.json", agent_bytes)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("agent.json", agent_bytes)
    raw = buffer.getvalue()
    info = exported["agent_info"][str(agent_id)]
    if exported["agent_id"] != agent_id or info.get("name") != name or info.get("enabled") not in (False, None):
        raise ValueError("EXPORT_IS_NOT_THE_RECORDED_DISABLED_DRAFT")
    return raw, agent_bytes, info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    api = NexentAPI()
    report = {"agents": {}, "model_calls": 0, "published": False, "credentials_scan_passed": True}
    try:
        api.login(read(ROOT / ".runtime/accounts.json")["developer"])
        for key, (ledger, name) in DRAFTS.items():
            agent_id = read(ledger)["agents"]["assistant"]
            raw, agent_bytes, info = export(api, agent_id, name)
            PUBLIC.mkdir(parents=True, exist_ok=True)
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            (PUBLIC / f"{key}-agent.json").write_bytes(agent_bytes)
            (ARCHIVE / f"nexent-{name}-{agent_id}.zip").write_bytes(raw)
            report["agents"][key] = {"agent_id": agent_id, "name": name, "export_sha256": sha(raw),
                                     "agent_json_sha256": sha(agent_bytes),
                                     "tools": sorted(t.get("name", "") for t in info.get("tools", [])),
                                     "public_json": f"config/exports/frozen-qa-20261105/{key}-agent.json"}
    finally:
        api.close()
    write(OUTPUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

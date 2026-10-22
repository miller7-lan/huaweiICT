"""Export the actual disabled native tree and verify all bundled Skill bytes."""
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_competition_package import inspect_content, safe_name, sha
from nexent_api import NexentAPI
from provision_ontology_crossdoc_native import check_drafts, check_skill, read, write, RUNTIME, OUTPUT, SKILL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-private-export", action="store_true", help="Validate the exact already downloaded export without HTTP")
    args = parser.parse_args()
    saved = read(RUNTIME / "native-drafts.json")
    if args.from_private_export:
        raw = (RUNTIME / "actual-native-export.zip").read_bytes()
    else:
        api = NexentAPI()
        try:
            api.login(read(ROOT / ".runtime/accounts.json")["developer"])
            check_drafts(api, saved)
            check_skill(api, saved)
            response = api.http.post(api.base + "/agent/export", json={"agent_id": saved["agents"]["controller"]},
                                     headers={"Authorization": "Bearer " + api.token})
            response.raise_for_status()
            raw = response.content
        finally:
            api.close()
    if len(raw) > 5 * 1024**2:
        raise ValueError("BOUNDED_NATIVE_EXPORT_REQUIRED")
    if not args.from_private_export:
        (RUNTIME / "actual-native-export.zip").write_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        expected = {"agent.json", "skills/" + SKILL + ".zip"}
        if set(bundle.namelist()) != expected or bundle.testzip() is not None:
            raise ValueError("EXACT_NATIVE_EXPORT_MEMBERS_REQUIRED")
        agent_bytes = bundle.read("agent.json")
        inspect_content("agent.json", agent_bytes)
        exported = json.loads(agent_bytes)
        if (set(exported["agent_info"]) != {str(i) for i in saved["agents"].values()}
                or exported["agent_id"] != saved["agents"]["controller"]):
            raise ValueError("ACTUAL_EXPORTED_TREE_IDS_DIFFER")
        with zipfile.ZipFile(io.BytesIO(bundle.read("skills/" + SKILL + ".zip"))) as skill:
            names = skill.namelist()
            if len(names) != len(set(names)) or skill.testzip() is not None:
                raise ValueError("INVALID_NATIVE_SKILL_EXPORT")
            matched = 0
            for name in names:
                safe_name(name)
                if name.endswith("/"):
                    continue
                content = skill.read(name)
                inspect_content(name, content)
                if name.endswith("SKILL.md"):
                    if content.decode("utf-8").replace("\r\n", "\n").strip() != (
                            ROOT / "config/skills" / SKILL / "SKILL.md").read_text("utf-8").replace("\r\n", "\n").strip():
                        raise ValueError("ACTUAL_EXPORTED_SKILL_BYTES_DIFFER")
                    matched += 1
            if matched != 1:
                raise ValueError("EXACT_ONE_EXPORTED_SKILL_REQUIRED")
    public_json = ROOT / "config/exports/ontology-crossdoc-native-20261021/agent.json"
    public_json.parent.mkdir(parents=True, exist_ok=True)
    public_json.write_bytes(agent_bytes)
    destination = ROOT / "artifacts/competition-20261021/nexent-ontology-crossdoc-native-export.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    report = {"status": "PASS_ACTUAL_DISABLED_TREE_EXPORT", "agent_count": 3,
              "actual_agent_ids": saved["agents"], "export_sha256": sha(raw),
              "agent_json_sha256": sha(agent_bytes), "skill_source_sha256": saved["skill_sha256"],
              "skill_content_verified": True, "credentials_scan_passed": True,
              "skill_comparison": "Exact full text after line-ending and outer-whitespace normalization",
              "archive": str(destination.relative_to(ROOT)).replace("\\", "/"),
              "actual_export_unmodified": True, "cross_tenant_import_tested": False,
              "model_calls": 0, "published": False}
    write(OUTPUT / "actual-export.json", report)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()

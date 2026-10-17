"""Export the actual bound Agent/Skill privately and verify both archive levels."""

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
import yaml

from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    private = RUNTIME / "asset-query"
    saved = json.loads((private / "nexent.json").read_text(encoding="utf-8"))
    api = NexentAPI()
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "passed": False}
    try:
        api.login(json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))["developer"])
        response = api.http.post(api.base + "/agent/export", headers={"Authorization": "Bearer " + api.token}, json={"agent_id": saved["agent_id"]})
        response.raise_for_status()
        data = response.content
        (private / "agent-export.zip").write_bytes(data)
        def skill_parts(text):
            _prefix, frontmatter, body = text.replace("\r\n", "\n").split("---", 2)
            return yaml.safe_load(frontmatter), body.strip()
        expected = skill_parts((ROOT / "config/skills/govdocs-evidence-query/SKILL.md").read_text(encoding="utf-8"))
        verified = False
        with zipfile.ZipFile(io.BytesIO(data)) as outer:
            if outer.testzip() is not None or "agent.json" not in outer.namelist():
                raise ValueError("Invalid outer export")
            for name in outer.namelist():
                if name.startswith("skills/") and name.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                        if inner.testzip() is not None:
                            raise ValueError("Invalid Skill archive")
                        for member in inner.namelist():
                            if member.endswith("SKILL.md") and skill_parts(inner.read(member).decode("utf-8")) == expected:
                                verified = True
            report["archive_members"] = outer.namelist()
        if not verified:
            raise ValueError("Actual Skill content not found in export")
        report.update(passed=True, agent_id=saved["agent_id"], skill_id=saved["skill_id"], version_no=saved["version_no"],
                      exported_skill_content_verified=True, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      storage="private .runtime/asset-query/agent-export.zip; connection settings must be reviewed before sharing")
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        api.close()
        destination = ROOT / "evidence/asset-query/export-20261015.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "summary": str(destination)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

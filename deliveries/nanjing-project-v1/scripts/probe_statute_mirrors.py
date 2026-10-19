"""One normal robots request per independently found official statute mirror."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import BOT, Fetcher


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ("https://jtgl.beijing.gov.cn", "/jgj/jgxx/flfg/fl/203777/index.html"),
    ("https://jjjcz.mee.gov.cn", "/djfg/gjflfg/fl/201110/t20111001_444437.html"),
]


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    records = []
    for origin, path in TARGETS:
        config = {"id": "statute-mirror-access-probe", "origin": origin, "allowed_paths": [path], "delay_seconds": 5}
        record = {"origin": origin, "document_url": origin+path, "checked_at": datetime.now(timezone.utc).isoformat(),
                  "operation": "robots_only", "document_requested": False, "user_agent_changed": False}
        try:
            status, headers, raw, final = Fetcher(store)._get(config, origin+"/robots.txt", robots=True)
            interpretable = status in (200, 404, 410) and not (status == 200 and re.search(br"<!doctype\s+html|<html\b", raw, re.I))
            allowed = False
            if interpretable:
                parser = RobotFileParser()
                parser.parse(raw.decode("utf-8-sig").splitlines() if status == 200 else [])
                allowed = parser.can_fetch(BOT, origin+path)
            record.update(status=status, content_type=headers.get("content-type"), bytes=len(raw), raw_sha256=fingerprint(raw),
                          policy_interpretable=bool(interpretable), policy_allows_path=allowed)
        except (OSError, ValueError) as error:
            record.update(error_type=type(error).__name__, error=str(error)[:200])
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    (ROOT / "evidence/knowledge-update/statute-mirror-access-probes.json").write_text(json.dumps({"records": records,
        "note": "Access-rule observations only; no source enabled and no body acquired"}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

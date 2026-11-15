"""Refuse evidence files that would publish private text, before they are committed.

  python scripts/check_public_evidence.py FILE.json [FILE.json ...]

A file fails when any JSON object in it holds a non-empty string under a key that carries
question, answer, claim, quote, source or reference text, or when any string looks like a
credential. Counts, ids, hashes, statuses and failure-code kinds pass. Exit 0: all files
pass; 1: something must be removed first (the key paths are printed, never the values).
"""
import json
import re
import sys
from pathlib import Path

TEXT_KEYS = {"question", "context", "answer", "final_answer", "display", "claim", "quote", "text", "sentence",
             "stop_reason", "unknowns", "key_points", "remark", "content", "messages", "evidence"}
SECRET = re.compile(r"sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*\S{8,}", re.I)


def findings(value, path="$"):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            if key in TEXT_KEYS and (isinstance(item, str) and item.strip()
                                     or isinstance(item, list) and any(isinstance(x, (str, dict)) and x for x in item)):
                found.append(here + ": private text field")
            else:
                found.extend(findings(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(findings(item, f"{path}[{index}]"))
    elif isinstance(value, str) and SECRET.search(value):
        found.append(path + ": looks like a credential")
    return found


def main():
    failed = False
    for name in sys.argv[1:]:
        problems = findings(json.loads(Path(name).read_text("utf-8")))
        failed |= bool(problems)
        print(json.dumps({"file": name, "passed": not problems, "problems": problems[:50]}, ensure_ascii=False))
    sys.exit(1 if failed or len(sys.argv) < 2 else 0)


if __name__ == "__main__":
    main()

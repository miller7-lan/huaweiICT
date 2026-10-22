"""Read only the workflow recovery guards; never tick, import providers or load secrets."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]


def inspect(root=ROOT):
    root = Path(root)
    config = json.loads((root / "config/decision/connections.json").read_text("utf-8"))
    path = root / ".runtime/agent-workflow/knowledge.sqlite3"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        state = {k: json.loads(v) for k, v in db.execute(
            "SELECT key,value FROM state WHERE key IN ('wf_control','wf_head')")}
        versions = db.execute("SELECT COUNT(*) FROM wf_versions").fetchone()[0]
    control = state.get("wf_control", {})
    at = control.get("heartbeat_at")
    age = time.time() - at if type(at) in (int, float) else None
    safe = (control.get("paused") is True and config.get("inference_enabled") is False
            and config.get("form_verifier_enabled") is False)
    return {"safe_to_recover": safe, "paused": control.get("paused"),
            "heartbeat_at": at, "heartbeat_age_seconds": age,
            "heartbeat_fresh": age is not None and -60 <= age <= 90,
            "head": state.get("wf_head"), "version_count": versions,
            "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-paused", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect()
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"safe_to_recover": False, "error": "WORKFLOW_STATE_UNAVAILABLE"}))
        raise SystemExit(1)
    print(json.dumps(result))
    if args.require_paused and not result["safe_to_recover"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

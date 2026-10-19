"""One fresh baseline native smoke run without republishing any original agent."""

import json

from benchmark_native import run_case
from nexent_api import NexentAPI, ROOT, RUNTIME


def main():
    state = json.loads((RUNTIME / "nexent-state.json").read_text(encoding="utf-8"))
    api = NexentAPI()
    try:
        record = run_case(api, state, "citizen", state["active_model"], 1)
        record["scope"] = "Single baseline native regression after M1 addition; original agents not republished"
        (ROOT / "evidence/m1-original-native-regression.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Original native regression", "PASS" if record["passed"] else "FAIL", record["stop_reasons"])
        raise SystemExit(0 if record["passed"] else 1)
    finally:
        api.close()


if __name__ == "__main__":
    main()

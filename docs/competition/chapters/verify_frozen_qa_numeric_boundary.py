"""Run three synthetic cases against the existing frozen QA verifier; no model or network.

Pass the project root containing src/gov_mcp/frozen_qa.py. This checks a pinned
historical source version; observed behavior is not an evaluation of real cases.
"""
import argparse
import hashlib
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project_root", type=Path)
args = parser.parse_args()
src = args.project_root / "src"
module_file = src / "gov_mcp" / "frozen_qa.py"
actual = hashlib.sha256(module_file.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
expected = "d3ce46c2c60f843a5f156ba11285026a08a4e5bdf4c3905f35ec491e81881ad4"
if actual != expected:
    raise SystemExit("Verifier source differs from the tested snapshot; refusing to import it.")
sys.path.insert(0, str(src))
from gov_mcp.frozen_qa import verify  # noqa: E402

units = [{"source_id": "SYNTHETIC", "locator": "P1", "text": "测试费用为200元。"}]
observed = {}
for number in ("200", "20", "21"):
    phrase = f"测试费用为{number}元。"
    answer = {"outcome": "answer", "answer": phrase,
              "assertions": [{"claim": phrase, "source_id": "SYNTHETIC", "locator": "P1",
                              "quote": "测试费用为200元。"}],
              "stop_reason": "", "unknowns": []}
    result = verify(answer, units)
    observed[number] = result["passed"]
    print(f"quote=200 answer={number} result={result}")
assert observed == {"200": True, "20": True, "21": False}, observed
print("scope=synthetic; existing verifier; no model, network, or real business data")


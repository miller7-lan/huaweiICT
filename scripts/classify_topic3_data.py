"""Run optional Jev classification on parsed synthetic assets; keep private sidecars."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.classification import classify_corpus, save_run


def classify_prepared(corpus, config_path, output_directory, *, provider_name=None):
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    if provider_name is not None:
        if provider_name not in {"jev", "vercel_jev_live"}: raise ValueError("INVALID_CLASSIFICATION_PROVIDER")
        config["provider"] = provider_name
    run = classify_corpus(corpus, config)
    path = save_run(run, output_directory)
    return run, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=".runtime/preparation/run/assets.json")
    parser.add_argument("--config", default="config/preparation/classification.json")
    parser.add_argument("--output", default=".runtime/preparation/classification")
    parser.add_argument("--provider", choices=["jev", "vercel_jev_live"], help="jev: TypeSafe official API (configured default); vercel_jev_live: explicit legacy gateway")
    args = parser.parse_args()
    destination = (ROOT / args.output).resolve()
    if not destination.is_relative_to(ROOT / ".runtime/preparation"):
        parser.error("Raw classification outputs must stay in .runtime/preparation")
    corpus = json.loads((ROOT / args.input).read_text(encoding="utf-8-sig"))
    run, path = classify_prepared(corpus, ROOT / args.config, destination, provider_name=args.provider)
    print(json.dumps({"output": str(path.relative_to(ROOT)), **run["summary"]}, ensure_ascii=False))
    return 2 if run["summary"]["error_codes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

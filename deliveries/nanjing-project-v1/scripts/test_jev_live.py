"""Small, explicitly authorized live test on fixed synthetic inputs; no publication."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from gov_mcp.classification import ClassificationError, JevProvider, ENDPOINT as OFFICIAL_ENDPOINT, classify_corpus, questions_for, validate_response
from gov_mcp.jev_gateway import VercelJevProvider, MODEL, GATEWAY_MODEL


def corpus_for(cases):
    return {"data_class": "synthetic", "dataset_id": "jev-live-engineering-examples", "assets": [
        {"asset_id": c["id"], "source_id": c["id"], "sha256": hashlib.sha256(c["text"].encode()).hexdigest(),
         "data_class": "synthetic", "title": c["title"], "modality": "text", "segments": [
             {"asset_id": c["id"], "segment_id": c["id"]+"-s1", "content": c["text"], "locator": {"kind": "line", "line": 1}}]}
        for c in cases]}


def set_metrics(pairs):
    tp = sum(len(set(a)&set(b)) for a, b in pairs)
    fp = sum(len(set(a)-set(b)) for a, b in pairs)
    fn = sum(len(set(b)-set(a)) for a, b in pairs)
    return {"exact_matches": sum(set(a)==set(b) for a,b in pairs), "total": len(pairs),
            "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "micro_f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.0}


def run_suite(provider):
    config = json.loads((ROOT/"config/preparation/classification.json").read_text("utf-8"))
    examples = json.loads((ROOT/"config/preparation/classification.examples.json").read_text("utf-8"))
    if examples["data_class"] != "synthetic" or len(examples["cases"]) != 9: raise ValueError("FIXED_SYNTHETIC_FIXTURE_REQUIRED")
    cases = examples["cases"]
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "running",
        "scope": "direct Jev only, no translation; nine engineering examples, not independent business accuracy",
        "input_translation_enabled": False,
        "data_class": "synthetic", "provider": provider.name, "model": MODEL,
        "endpoint": OFFICIAL_ENDPOINT if provider.name == "jev" else "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
        "transport_model": MODEL if provider.name == "jev" else GATEWAY_MODEL,
        "max_http_calls": 32, "max_request_bytes": 16000, "other_model_calls": 0,
        "business_rules_published": False, "workflow_heartbeat_auto_enabled": False}
    try:
        checks = {"supported": "模拟原文是否明确要求提交申请表？", "unsupported": "模拟原文是否明确要求缴费100元？",
                  "contradiction": "模拟原文是否明确表示不用提交申请表？"}
        questions = {k: {"type": "noul", "instructions": v} for k, v in checks.items()}
        smoke = validate_response(provider.evaluate({"data_class": "synthetic", "text": "模拟原文：申请时须提交申请表。仅供工程测试。"}, questions, MODEL), questions, MODEL)
        report["support_smoke"] = {"answers": smoke["answers"], "expected_positive": ["supported"],
            "expected_negative": ["unsupported", "contradiction"], "all_expected":
            smoke["answers"]["supported"]["noul"] >= .8 and all(smoke["answers"][k]["noul"] <= .2 for k in ("unsupported", "contradiction"))}
        run = classify_corpus(corpus_for(cases), config, provider)
        report["classification"] = run
        if run["summary"]["status"] != "completed":
            raise ClassificationError(next(iter(run["summary"]["error_codes"]), "CLASSIFICATION_FAILED"))
        pairs, domains, purposes, routes = [], [], [], []
        for case, record in zip(cases, run["records"]):
            pairs.append({"id": case["id"], "expected_type": case["document_type"], "actual_type": record["document_type"]["choice"],
                          "confidence": record["document_type"]["confidence"], "status": record["status"]})
            domains.append((record["domains"]["labels"], case["domains"]))
            purposes.append((record["purposes"]["labels"], case["purposes"]))
            routes.append((record["routes"], case["routes"]))
        report["metrics"] = {"document_types": {"correct": sum(p["actual_type"]==p["expected_type"] for p in pairs), "total": len(pairs), "cases": pairs},
            "domains": set_metrics(domains), "purposes": set_metrics(purposes), "routes": set_metrics(routes),
            "pending_review_fraction": run["summary"]["pending_review_fraction"]}
        # Reverse only the Choice options: 9 extra requests, not a second full suite.
        reversed_results = []
        for case, original in zip(cases, run["documents"]):
            state = {"title": case["title"], "segments": [{"segment_id": case["id"]+"-s1", "content": case["text"]}]}
            qs = questions_for(config, "document", True)
            answer = validate_response(provider.evaluate(state, qs, MODEL), qs, MODEL)
            choice = answer["answers"]["document_type"]
            reversed_results.append({"id": case["id"], "original": original["windows"][0]["document_type"]["choice"],
                                     "reversed": choice["choice"], "confidence": choice["confidence"]})
        report["option_order"] = {"cases": reversed_results, "changed": sum(r["original"] != r["reversed"] for r in reversed_results), "total": len(cases)}
        originals = {s["segment_id"] for a in corpus_for(cases)["assets"] for s in a["segments"]}
        routed = {s["segment_id"] for bundle in run["bundles"].values() for s in bundle}
        report["all_originals_preserved"] = originals == routed
        report["status"] = "completed"  # Quality metrics are reported separately; not a quality pass.
    except ClassificationError as error:
        report.update(status="blocked" if str(error) in {"MISSING_API_KEY", "PAYMENT_REQUIRED", "ACCESS_DENIED", "AUTHENTICATION_FAILED", "ACCESS_RESTRICTED"} else "failed", error_code=str(error))
    report["http_attempts"] = provider.attempts
    report["receipts"] = deepcopy(provider.receipts)
    report["known_success_cost_usd"] = str(provider.known_cost) if hasattr(provider, "known_cost") else None
    report["cost_complete"] = bool(provider.receipts) and all(r["status"] == "ok" and r["cost_usd"] is not None for r in provider.receipts)
    report["cost_scope"] = ("official response token usage only; monetary cost unknown, verify TypeSafe billing"
        if provider.name == "jev" else "reported successful-response cost only; failed attempts may also be billed")
    return report


def create_provider(provider_name, key_file=None):
    """Credentials are provider-specific; never reuse a gateway key for TypeSafe."""
    field = "TYPESAFE_API_KEY" if provider_name == "jev" else "AI_GATEWAY_API_KEY"
    key = os.environ.get(field, "")
    if not key and key_file is not None and key_file.exists():
        key = json.loads(key_file.read_text("utf-8-sig")).get(field, "")
    return JevProvider(key, max_calls=32) if provider_name == "jev" else VercelJevProvider(key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Explicitly allow Jev network calls on synthetic examples")
    parser.add_argument("--provider", choices=["jev", "vercel_jev_live"], default="jev", help="Default: TypeSafe official API; gateway requires explicit selection")
    parser.add_argument("--key-file", type=Path, help="Optional server-side JSON with the selected provider's credential field")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live: parser.error("--live is required; ordinary regression tests never call providers")
    key_file = args.key_file or ROOT/(".runtime/jev/official.credentials.json" if args.provider == "jev" else ".runtime/jev/credentials.json")
    output = args.output or ROOT/("evidence/jev-official/latest.json" if args.provider == "jev" else "evidence/jev-live/latest.json")
    report = run_suite(create_provider(args.provider, key_file))
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [ROOT/"src/gov_mcp/jev_gateway.py", ROOT/"src/gov_mcp/classification.py", Path(__file__).resolve(),
               ROOT/"config/preparation/classification.json", ROOT/"config/preparation/classification.examples.json"]
    report["source_sha256"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", "utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "error_code", "http_attempts", "known_success_cost_usd")}, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__": raise SystemExit(main())

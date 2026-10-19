"""One local call to the existing OCR interface, then downstream persistence checks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_pdf import windows_ocr
from gov_mcp.matter_graph import safe_child
from gov_mcp.matter_ocr_handoff import check_original, consume
from private_runtime import ensure_private_directory


def write(path, value): path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--run-existing-windows-interface", action="store_true")
    args = parser.parse_args()
    if not args.run_existing_windows_interface: parser.error("Explicit existing-interface call required")
    if args.runtime.exists() and any(args.runtime.iterdir()): parser.error("Existing OCR integration activity retained")
    ensure_private_directory(args.runtime)
    request_path = ROOT / "data/real-matter-closure/nanjing/ocr-handoff/request-v1.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    original_path = safe_child(ROOT, request["original_repository_path"]); original = original_path.read_bytes()
    check_original(request, original)
    implementation = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
        ("src/gov_mcp/knowledge_pdf.py", "src/gov_mcp/knowledge_ocr_windows.ps1")}
    report = {"schema": 1, "started_at": datetime.now(timezone.utc).isoformat(), "request_id": request["request_id"],
        "operator": "automated_local_interface_integration", "human_teammate_submission": False,
        "existing_interface_attempts": 0, "new_ocr_implementation": False, "model_downloads": 0,
        "cloud_model_calls": 0, "human_business_review": False, "formal_publication": False,
        "upstream_implementation_sha256": implementation}
    write(args.runtime / "started.json", report)
    try:
        report["existing_interface_attempts"] = 1
        response = windows_ocr(original_path, request["language"])
        write(args.runtime / "upstream-response.json", response)
        result = consume(request, original, response, KnowledgeStore.initialize(args.runtime / "candidate-registry"))
        for name, expected in implementation.items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected: raise ValueError("Upstream OCR implementation changed")
        candidate = result.pop("candidate")
        write(args.runtime / "candidate-document.json", candidate)
        report.update(status="PASSED", downstream=result, upstream_implementation_unchanged=True,
            anchor_observations=[{"pdf_page": i + 1, "return_old_card_phrase_observed": "交回原证" in "".join(c["text"].split()),
                "statutory_60_days_phrase_observed": "六十日" in "".join(c["text"].split())} for i, c in enumerate(candidate["chunks"])])
    except Exception as error:
        report.update(status="BLOCKED_EXISTING_OCR_OR_CONTRACT", error_type=type(error).__name__)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write(args.runtime / "receipt.json", report)
    output = ROOT / "evidence/real-matter-closure/ocr-handoff"
    output.mkdir(parents=True, exist_ok=True)
    write(output / "interface-receipt.json", report)
    if report["status"] == "PASSED":
        target = ROOT / "data/real-matter-closure/nanjing/ocr-handoff/result-v1"
        if target.exists(): raise ValueError("Existing sealed OCR result preserved")
        target.mkdir()
        for name in ("upstream-response.json", "candidate-document.json", "receipt.json"):
            (target / name).write_bytes((args.runtime / name).read_bytes())
    print(json.dumps({"status": report["status"], "existing_interface_attempts": report["existing_interface_attempts"],
        "pages": report.get("downstream", {}).get("pages"), "cloud_model_calls": 0, "error_type": report.get("error_type")}))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__": raise SystemExit(main())

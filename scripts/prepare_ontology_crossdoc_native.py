"""Prepare a distinct, unspent native campaign without activating the ontology."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.crossdoc_reasoning import load_bundle
from gov_mcp.ontology_crossdoc_preview_server import open_preview
from private_runtime import ensure_private_directory

RUNTIME = ROOT / ".runtime/ontology-crossdoc-native-20261021"
OUTPUT = ROOT / "evidence/ontology-crossdoc-native-20261021"
DEMONSTRATION = ROOT / "evidence/ontology-crossdoc-20261020/demonstration-20261021T111607928497Z.json"
CONFIRMATION = ROOT / "evidence/ontology-crossdoc-20261020/structural-confirmation-20261021.json"
SOURCE = ROOT / ".runtime/ontology-crossdoc-20261020/archive"
ONTOLOGY = ROOT / ".runtime/ontology-crossdoc-20261020/ontology"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def prepare():
    demonstration = json.loads(DEMONSTRATION.read_text("utf-8"))
    confirmation = json.loads(CONFIRMATION.read_text("utf-8"))
    if (demonstration.get("status") != "PASS_LOCAL_ONTOLOGY_CROSSDOC"
            or not all(demonstration["checks"].values())
            or demonstration["current_head"] != demonstration["baseline"]
            or confirmation.get("actor", {}).get("kind") != "human"
            or confirmation["candidate_id"] != demonstration["candidate_id"]
            or confirmation["expected_head"] != demonstration["baseline"]):
        raise ValueError("COMPLETED_REVIEWED_AND_ROLLED_BACK_LOCAL_DEMO_REQUIRED")
    bundle = load_bundle(SOURCE)
    ensure_private_directory(RUNTIME)
    # Copy only checked sources and gates. Never duplicate native ledgers.
    for relative in ("manifest.json", "gates.json", *[e["raw_path"] for e in bundle["manifest"]["sources"].values()]):
        target = RUNTIME / relative
        if target.exists():
            if target.read_bytes() != (SOURCE / relative).read_bytes():
                raise ValueError("EXISTING_NATIVE_ARCHIVE_DIFFERS")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / relative, target)
    if load_bundle(RUNTIME) != bundle:
        raise ValueError("NATIVE_ARCHIVE_COPY_CHANGED")
    binding = {"ontology_root": str(ONTOLOGY), "baseline": demonstration["baseline"],
               "expected_version": demonstration["activated_version"],
               "candidate_id": demonstration["candidate_id"],
               "confirmation_sha256": hashlib.sha256(CONFIRMATION.read_bytes()).hexdigest()}
    path = RUNTIME / "ontology-binding.json"
    if path.exists() and json.loads(path.read_text("utf-8")) != binding:
        raise ValueError("EXISTING_NATIVE_ONTOLOGY_BINDING_CHANGED")
    if not path.exists():
        write(path, binding)
    if not (RUNTIME / "access.json").exists():
        write(RUNTIME / "access.json", {"mcp_token": secrets.token_urlsafe(48)})
    preview = open_preview(RUNTIME)
    status = preview.workspace.status()
    if status["head"] != binding["baseline"]:
        raise ValueError("EXPECTED_ROLLED_BACK_BASELINE_REQUIRED")
    approved = preview.workspace._version(binding["expected_version"])
    preview.workspace._guard_version(approved, preview.workspace._current())
    record = next(c for c in status["candidates"] if c["id"] == binding["candidate_id"])
    if record["status"] != "accepted_structure_only" or record["version"] != binding["expected_version"]:
        raise ValueError("EXACT_ACCEPTED_STRUCTURAL_VERSION_REQUIRED")
    plan = {"prepared_at": datetime.now(timezone.utc).isoformat(),
            "status": "PREPARED_ROLLED_BACK_AWAITING_NATIVE_AUTHORIZATION", "port": 8785,
            "service": "govkb_ontology_crossdoc_preview", "campaign": preview.counts(),
            "candidate_id": binding["candidate_id"], "baseline": binding["baseline"],
            "reviewed_version": binding["expected_version"], "archive_binding": bundle["binding"],
            "current_ontology_head": status["head"], "ontology_activated": False,
            "confirmation_record": str(CONFIRMATION.relative_to(ROOT)).replace("\\", "/"),
            "model_calls": 0, "http_calls": 0, "old_budget_reset": False,
            "business_conclusion_status": "PAUSED_UNREVIEWED", "formally_published": False,
            "native_execution_complete": False}
    write(OUTPUT / "preparation.json", plan)
    return plan


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(prepare(), ensure_ascii=True))

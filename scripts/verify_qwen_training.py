"""Verify saved QLoRA artifacts without loading the base model or using the GPU."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z", args.run_id):
        raise ValueError("Expected the timestamp ID of a completed training run")
    directory = ROOT / ".runtime/training/runs" / args.run_id
    report = json.loads((directory / "result.json").read_bytes())
    if not report["training_completed"] or report["optimizer_steps"] < 1 or report["stage"] != "completed":
        raise ValueError("Training was not completed")
    if not report["optimizer_probe_changed"] or report["production_model_changed"]:
        raise ValueError("Unexpected training/deployment result")
    for name, expected in report["adapter_files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid artifact filename")
        actual = hashlib.sha256((directory / "adapter" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Saved artifact fingerprint mismatch")
    import torch
    from safetensors.torch import load_file
    weights = load_file(directory / "adapter/adapter_model.safetensors", device="cpu")
    if not weights or any("lora_A" not in name and "lora_B" not in name for name in weights):
        raise ValueError("Unexpected non-adapter tensor")
    if any(not torch.isfinite(tensor).all() for tensor in weights.values()):
        raise ValueError("Non-finite saved adapter tensor")
    if not any(bool(torch.any(tensor != 0)) for name, tensor in weights.items() if "lora_B" in name):
        raise ValueError("All initially-zero LoRA B tensors are still zero")
    parameters = sum(tensor.numel() for tensor in weights.values())
    if parameters != report["trainable_parameters"]:
        raise ValueError("Saved and trained parameter counts differ")
    result = {
        "run_id": args.run_id, "saved_file_hashes_match": True,
        "adapter_tensors_finite": True, "lora_B_updated": True,
        "tensor_count": len(weights), "adapter_parameters": parameters,
        "optimizer_steps": report["optimizer_steps"],
        "diagnostic_before": report["before"]["matching_draft_labels"],
        "diagnostic_after": report["after"]["matching_draft_labels"],
        "diagnostic_rows": report["after"]["rows"],
        "independent_accuracy": False, "base_model_reloaded": False,
        "production_model_changed": False, "passed": True,
    }
    output = ROOT / "evidence/qwen-training" / ("artifact-check-" + args.run_id + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

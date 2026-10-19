"""Load a completed local topic adapter and classify one question; no server changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--save-demo-proof", action="store_true", help="Save the supplied non-private demo question and prediction as public engineering evidence")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z", args.run_id) or not 1 <= len(args.question.strip()) <= 200:
        raise ValueError("Invalid run ID or question length")
    directory = ROOT / ".runtime/training/runs" / args.run_id
    report = json.loads((directory / "result.json").read_bytes())
    if not report["training_completed"] or report.get("model_source") != "official_Qwen_ModelScope_per_file_sha256":
        raise ValueError("Expected a completed official ModelScope-base experiment")
    spec_path = ROOT / "config/training/qwen3-4b.modelscope.json"
    if hashlib.sha256(spec_path.read_bytes()).hexdigest() != report["model_source_spec_sha256"]:
        raise ValueError("Base model specification differs from training")
    for name, expected in report["adapter_files"].items():
        if Path(name).name != name or hashlib.sha256((directory / "adapter" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Saved adapter fingerprint mismatch")
    from download_qwen_training_base import verify_base
    from train_qwen_topic import SYSTEM, TOPICS
    base = ROOT / ".runtime/training/base/Qwen3-4B-modelscope"
    verify_base(base, json.loads(spec_path.read_bytes()))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import PeftModel
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU unavailable")
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(base, local_files_only=True, trust_remote_code=False,
        use_safetensors=True, device_map={"":0}, dtype=dtype, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype))
    model = PeftModel.from_pretrained(model, directory / "adapter", is_trainable=False, local_files_only=True)
    model.eval()
    prompt = tokenizer.apply_chat_template([{"role":"system","content":SYSTEM},{"role":"user","content":args.question}],
        tokenize=False,add_generation_prompt=True,enable_thinking=False)
    inputs = tokenizer(prompt,return_tensors="pt",add_special_tokens=False).to("cuda")
    if inputs["input_ids"].shape[1] + 32 > 384:
        raise ValueError("Question exceeds this experiment's token budget")
    with torch.inference_mode():
        generated = model.generate(**inputs,max_new_tokens=32,do_sample=False,pad_token_id=tokenizer.eos_token_id)
    text = tokenizer.decode(generated[0,inputs["input_ids"].shape[1]:],skip_special_tokens=True).strip()
    result = json.loads(text)
    if not isinstance(result,dict) or set(result)!={"topic"} or result["topic"] not in TOPICS:
        raise ValueError("Invalid classifier output; send for review rather than accepting it")
    proof = {"run_id":args.run_id,"question":args.question,"output":result,
        "base_and_adapter_reloaded":True,"weights_verified":True,"local_inference":True,
        "independent_accuracy":False,"production_model_changed":False}
    if args.save_demo_proof:
        (ROOT/"evidence/qwen-training/inference-smoke.json").write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()

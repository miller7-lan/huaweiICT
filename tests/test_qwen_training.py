import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_qwen_training import build_rows, digest
from train_qwen_topic import encode_example, load_dataset
from download_qwen_training_base import validate_spec, verify_base


def config():
    return json.loads((ROOT / "config/training/qwen-topic-smoke.json").read_text(encoding="utf-8"))


def fixture_dataset():
    cfg = config()
    return {"cases": [{"id": key, "question": "工程问题" + key, "category": "materials"}
                      for key in cfg["families"]]}


def test_families_are_separated_and_all_topics_train():
    cfg = config()
    dataset = fixture_dataset()
    for case, category in zip(dataset["cases"], ["materials", "location", "fee", "privacy"]):
        case["category"] = category
    # KQ01/02/03/04 are all training families.
    a, b = build_rows(dataset, cfg)
    assert len(a) == 23 and len(b) == 7
    assert not {x["family"] for x in a} & {x["family"] for x in b}
    assert all("answer" not in x for x in a + b)


def test_unknown_category_and_duplicate_ids_rejected():
    cfg = config()
    dataset = fixture_dataset()
    dataset["cases"][0]["category"] = "unregistered"
    with pytest.raises(KeyError):
        build_rows(dataset, cfg)
    dataset["cases"][0] = dataset["cases"][1]
    with pytest.raises(ValueError, match="unique"):
        build_rows(dataset, cfg)


def test_missing_class_not_silently_accepted():
    with pytest.raises(ValueError, match="no example"):
        build_rows(fixture_dataset(), config())


def test_draft_answers_cannot_enter_training_rows():
    cfg = config()
    dataset = fixture_dataset()
    for case, category in zip(dataset["cases"], ["materials", "location", "fee", "privacy"]):
        case["category"] = category
        case["ai_reference_answer"] = "不应复制"
    a, b = build_rows(dataset, cfg)
    assert all("ai_reference_answer" not in x for x in a + b)


def test_changed_data_rejected_before_model_import(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "policy_answers_used_as_training_targets": False,
        "historical_model_outputs_used_as_training_targets": False,
        "experiment_sha256": digest(b"{}"),
        "dataset": {"train": {"sha256": "0" * 64}},
    }), encoding="utf-8")
    (tmp_path / "experiment.json").write_text("{}", encoding="utf-8")
    (tmp_path / "train.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        load_dataset(tmp_path)


class ToyTokenizer:
    eos_token = "!"

    def apply_chat_template(self, messages, **kwargs):
        return "prompt>" + messages[1]["content"]

    def encode(self, text, **kwargs):
        return list(text.encode("utf-8"))


def test_only_assistant_target_has_loss_and_no_silent_truncation():
    row = {"question": "问题", "topic": "fees_time"}
    tok = ToyTokenizer()
    encoded = encode_example(tok, row, 100)
    boundary = len(tok.encode("prompt>问题"))
    assert encoded["labels"][:boundary] == [-100] * boundary
    assert encoded["labels"][boundary:] == encoded["input_ids"][boundary:]
    with pytest.raises(ValueError, match="budget"):
        encode_example(tok, row, 5)


def test_model_manifest_rejects_path_escape_and_unpinned_revisions():
    path = ROOT / "config/training/qwen3-4b.modelscope.json"
    spec = json.loads(path.read_bytes())
    validate_spec(spec)
    spec["files"][0]["Path"] = "../config.json"
    with pytest.raises(ValueError, match="filename"):
        validate_spec(spec)
    spec = json.loads(path.read_bytes())
    spec["files"][0]["Revision"] = "master"
    with pytest.raises(ValueError, match="revision"):
        validate_spec(spec)


def test_model_integrity_rejects_corrupt_weights_before_loading(tmp_path):
    spec = json.loads((ROOT / "config/training/qwen3-4b.modelscope.json").read_bytes())
    with pytest.raises(ValueError, match="integrity"):
        verify_base(tmp_path, spec)

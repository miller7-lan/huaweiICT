"""Preflight of an evaluation round: what blocks it and what it may be called (synthetic data)."""
import json

import flow_v3_preflight as preflight

FACTS = {fact: True for fact in preflight.INDEPENDENCE}
BASE = {"round": "r", "commit": "abc1234", "rules": "strict", "arms": ["baseline", "single_shot", "flow_v3"], **FACTS}
UNITS = [{"source_id": "D", "locator": "段1", "text": "甲"}]


def frozen_inputs(tmp_path):
    from gov_mcp import frozen_qa

    questions = [{"id": f"X{i}", "question": f"题{i}"} for i in range(1, 5)]
    reference = [{"id": f"X{i}", "expected_outcome": "answer" if i < 4 else "stop", "category": category,
                  "key_points": ["甲"] if i < 4 else []}
                 for i, category in enumerate(("comparison", "temporal", "inference", "should_stop"), 1)]
    paths = {name: tmp_path / (name + ".json") for name in ("questions", "reference", "corpus")}
    for name, value in (("questions", questions), ("reference", reference), ("corpus", {"units": UNITS})):
        paths[name].write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    digest = frozen_qa.sha(json.dumps(UNITS, ensure_ascii=False, sort_keys=True))
    return {**BASE, "corpus_unit_sha256": digest}, paths


def run(declared, **observed):
    return preflight.evaluate(declared, commit=observed.pop("commit", "abc1234"), dirty=observed.pop("dirty", []),
                              **observed)


def test_all_conditions_true_is_formal_and_any_unknown_downgrades(tmp_path):
    declared, paths = frozen_inputs(tmp_path)
    assert run(declared, **paths)["label"] == "formal" and run(declared, **paths)["name_to_use"] == "正式评测"
    for fact, consequence in preflight.INDEPENDENCE.items():
        for value in (False, None, "yes", 1):
            result = run({**declared, fact: value}, **paths)
            assert result["label"] == "development" and result["discounts"] == [consequence]
        missing = {k: v for k, v in declared.items() if k != fact}
        assert run(missing, **paths)["name_to_use"] == "开发对比"


def test_unfrozen_profile_wrong_commit_or_dirty_tree_blocks():
    assert "RULES_NOT_FROZEN" in run({**BASE, "rules": None})["blocked"][0]
    assert "ARMS_NOT_FROZEN" in run({**BASE, "arms": ["other"]})["blocked"][0]
    assert run(BASE, commit="def5678")["label"] == "BLOCKED"
    assert "WORKING_TREE_NOT_CLEAN" in run(BASE, dirty=["src/gov_mcp/frozen_qa_flow_v3.py"])["blocked"][0]


def test_questions_need_a_valid_reference_with_enough_should_stop(tmp_path):
    declared, paths = frozen_inputs(tmp_path)
    good = run(declared, **paths)
    assert good["label"] == "formal" and good["observed"]["reference"]["expected"] == {"answer": 3, "stop": 1}
    assert "甲" not in json.dumps(good, ensure_ascii=False)
    for name in paths:
        result = run(declared, **{key: path for key, path in paths.items() if key != name})
        assert result["label"] == "BLOCKED" and any(code.startswith(name.upper() + "_REQUIRED") for code in result["blocked"])
    r = paths["reference"]
    reference = json.loads(r.read_text("utf-8"))
    reference[3].update(expected_outcome="answer", key_points=["丁"])
    r.write_text(json.dumps(reference, ensure_ascii=False), "utf-8")
    assert any("REFERENCE_STOP_CATEGORY_MISMATCH" in code for code in run(declared, **paths)["blocked"])
    reference[3]["category"] = "comparison"
    r.write_text(json.dumps(reference, ensure_ascii=False), "utf-8")
    assert "TOO_FEW_SHOULD_STOP_QUESTIONS" in run(declared, **paths)["blocked"]


def test_service_profile_and_corpus_must_match_the_round(tmp_path):
    declared, paths = frozen_inputs(tmp_path)
    unit_sha = declared["corpus_unit_sha256"]
    assert run(declared, **paths, health={"rules": "strict", "corpus_sha256": unit_sha})["label"] == "formal"
    mismatch = run(declared, **paths, health={"rules": "light", "corpus_sha256": unit_sha})
    assert mismatch["label"] == "BLOCKED" and "SERVICE_RULES_MISMATCH" in mismatch["blocked"][0]
    assert "SERVICE_CORPUS_MISMATCH" in run(declared, **paths,
                                            health={"rules": "strict", "corpus_sha256": "0" * 64})["blocked"][0]
    assert "CORPUS_MISMATCH" in run(BASE, **paths)["blocked"][0]

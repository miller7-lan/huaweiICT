"""Preflight of an evaluation round: what blocks it and what it may be called (synthetic data)."""
import json

import flow_v3_preflight as preflight

FACTS = {fact: True for fact in preflight.INDEPENDENCE}
BASE = {"round": "r", "commit": "abc1234", "rules": "strict", "arms": ["baseline", "single_shot", "flow_v3"], **FACTS}


def run(declared, **observed):
    return preflight.evaluate(declared, commit=observed.pop("commit", "abc1234"), dirty=observed.pop("dirty", []),
                              **observed)


def test_all_conditions_true_is_formal_and_any_unknown_downgrades():
    assert run(BASE)["label"] == "formal" and run(BASE)["name_to_use"] == "正式评测"
    for fact, consequence in preflight.INDEPENDENCE.items():
        for value in (False, None, "yes", 1):
            result = run({**BASE, fact: value})
            assert result["label"] == "development" and result["discounts"] == [consequence]
        missing = {k: v for k, v in BASE.items() if k != fact}
        assert run(missing)["name_to_use"] == "开发对比"


def test_unfrozen_profile_wrong_commit_or_dirty_tree_blocks():
    assert "RULES_NOT_FROZEN" in run({**BASE, "rules": None})["blocked"][0]
    assert "ARMS_NOT_FROZEN" in run({**BASE, "arms": ["other"]})["blocked"][0]
    assert run(BASE, commit="def5678")["label"] == "BLOCKED"
    assert "WORKING_TREE_NOT_CLEAN" in run(BASE, dirty=["src/gov_mcp/frozen_qa_flow_v3.py"])["blocked"][0]


def test_questions_need_a_valid_reference_with_enough_should_stop(tmp_path):
    questions = [{"id": f"X{i}", "question": f"题{i}"} for i in range(1, 5)]
    reference = [{"id": "X1", "expected_outcome": "answer", "category": "comparison", "key_points": ["甲"]},
                 {"id": "X2", "expected_outcome": "answer", "category": "temporal", "key_points": ["乙"]},
                 {"id": "X3", "expected_outcome": "answer", "category": "inference", "key_points": ["丙"]},
                 {"id": "X4", "expected_outcome": "stop", "category": "should_stop", "key_points": []}]
    q, r = tmp_path / "q.json", tmp_path / "r.json"
    q.write_text(json.dumps(questions, ensure_ascii=False), "utf-8")
    r.write_text(json.dumps(reference, ensure_ascii=False), "utf-8")
    good = run(BASE, questions=q, reference=r)
    assert good["label"] == "formal" and good["observed"]["reference"]["expected"] == {"answer": 3, "stop": 1}
    assert "甲" not in json.dumps(good, ensure_ascii=False)
    assert "REFERENCE_REQUIRED" in run(BASE, questions=q)["blocked"][0]
    reference[3] = {"id": "X4", "expected_outcome": "answer", "category": "comparison", "key_points": ["丁"]}
    r.write_text(json.dumps(reference, ensure_ascii=False), "utf-8")
    assert "TOO_FEW_SHOULD_STOP_QUESTIONS" in run(BASE, questions=q, reference=r)["blocked"]


def test_service_profile_and_corpus_must_match_the_round(tmp_path):
    from gov_mcp import frozen_qa
    units = [{"source_id": "D", "locator": "段1", "text": "甲"}]
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"units": units}, ensure_ascii=False), "utf-8")
    unit_sha = frozen_qa.sha(json.dumps(units, ensure_ascii=False, sort_keys=True))
    declared = {**BASE, "corpus_unit_sha256": unit_sha}
    assert run(declared, corpus=corpus, health={"rules": "strict", "corpus_sha256": unit_sha})["label"] == "formal"
    mismatch = run(declared, corpus=corpus, health={"rules": "light", "corpus_sha256": unit_sha})
    assert mismatch["label"] == "BLOCKED" and "SERVICE_RULES_MISMATCH" in mismatch["blocked"][0]
    assert "SERVICE_CORPUS_MISMATCH" in run(declared, corpus=corpus,
                                            health={"rules": "strict", "corpus_sha256": "0" * 64})["blocked"][0]
    assert "CORPUS_MISMATCH" in run(BASE, corpus=corpus)["blocked"][0]

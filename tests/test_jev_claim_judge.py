import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jev_claim_judge as judge  # noqa: E402


def record(qid, method, assertions):
    return json.dumps({"question_id": qid, "compared_as": method, "final": {"assertions": assertions}}, ensure_ascii=False)


LINES = [
    record("Q1", "flow_v3", [{"claim": "工本费20元", "quote": "收取工本费每证20元", "source_id": "A", "locator": "1"},
                            {"claim": "", "quote": "空结论不送", "source_id": "A", "locator": "2"}]),
    record("Q2", "baseline", [{"claim": "时限60日", "quote": "60个工作日", "source_id": "B", "locator": "3"}]),
    json.dumps({"question_id": "Q3", "compared_as": "single_shot", "final": None}),
]


class FakeProvider:
    def __init__(self, scores, fail_at=None):
        self.scores, self.fail_at, self.attempts, self.receipts, self.sent = scores, fail_at, 0, [], []

    def evaluate(self, state, questions, model):
        self.attempts += 1
        if self.fail_at == self.attempts:
            raise judge.ClassificationError("ACCESS_DENIED")
        self.sent.append(state)
        answers = {k: {"type": "noul", "noul": self.scores.pop(0)} for k in questions}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_pairs_skip_empty_claims_and_missing_finals():
    pairs = judge.pairs_from_records(LINES)
    assert [(p["question_id"], p["method"], p["index"]) for p in pairs] == [("Q1", "flow_v3", 0), ("Q2", "baseline", 0)]


def test_only_claim_quote_and_source_leave_and_verdicts_follow_thresholds():
    provider = FakeProvider([0.95, 0.5])
    run = judge.judge(judge.pairs_from_records(LINES), provider, max_calls=5)
    assert [r["verdict"] for r in run["results"]] == ["supported", "uncertain"]
    sent = provider.sent[0]["pairs"]
    assert all(set(v) == {"claim", "quote", "source_id"} for v in sent.values())
    summary = judge.public_summary(run, 2)
    text = json.dumps(summary, ensure_ascii=False)
    assert "工本费" not in text and "60个工作日" not in text
    assert summary["by_method"] == {"baseline": {"uncertain": 1}, "flow_v3": {"supported": 1}}


def test_batches_respect_question_and_byte_limits():
    pairs = [{"question_id": "Q", "method": "m", "index": i, "claim": "结论" * 20, "quote": "原文" * 40,
              "source_id": "S", "locator": "L"} for i in range(80)]
    groups, skipped = judge.batches(pairs)
    assert not skipped and sum(len(g) for g in groups) == 80
    assert all(len(g) <= judge.MAX_QUESTIONS and judge.size(g) <= judge.MAX_BYTES for g in groups)
    huge = [{**pairs[0], "quote": "长" * 20000}]
    assert judge.batches(huge) == ([], huge)


def test_an_error_or_the_budget_stops_the_run():
    pairs = judge.pairs_from_records(LINES) * 40
    run = judge.judge(pairs, FakeProvider([0.9] * 200, fail_at=2), max_calls=10)
    assert run["errors"] == ["ACCESS_DENIED"] and run["http_attempts"] == 2
    run = judge.judge(pairs, FakeProvider([0.9] * 200), max_calls=1)
    assert run["errors"] == ["CALL_BUDGET_REACHED"] and run["http_attempts"] == 1

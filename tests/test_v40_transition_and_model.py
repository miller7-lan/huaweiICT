"""V4.0: transition gate and the Bailian (Kimi) cloud profile."""
import json

import httpx
import pytest

from gov_mcp import deepseek, frozen_qa
from gov_mcp import frozen_qa_flow_v3 as flow

Q_TRANSITION = "两篇解读的时限写法不同。一份申请在新解读发出前已受理、后来仍未领取，能否认定旧申请适用新时限？"
Q_PLAIN = "新旧两篇解读对普通居民身份证办理时限的写法有什么区别？"


def answer(unknowns):
    return {"outcome": "answer", "answer": "两篇解读分别记载了25日和15个工作日。", "assertions": [],
            "unknowns": unknowns, "stop_reason": ""}


def test_gate_stops_when_model_names_missing_transition_basis():
    final, kind, origin = flow.transition_gate(Q_TRANSITION, answer(["缺少新措施的过渡条款和施行日期。"]), "answer_with_gaps")
    assert (final["outcome"], kind, origin) == ("stop", "stop", flow.TRANSITION_STOP)
    assert "过渡安排或施行日期" in final["answer"] and final["stop_reason"] == flow.TRANSITION_STOP


def test_gate_accepts_applicability_wording_without_the_word_transition():
    assert flow.transition_unsettled(Q_TRANSITION, answer(["缺少已受理未领取申请适用哪一时限的规定依据。"]))


@pytest.mark.parametrize("question,unknowns", [
    (Q_PLAIN, ["缺少新措施的施行日期。"]),                 # not a transition question
    (Q_TRANSITION, ["缺少邮寄费用标准。"]),                 # gap unrelated to transition
    (Q_TRANSITION, []),                                    # complete answer
])
def test_gate_leaves_other_answers_unchanged(question, unknowns):
    final = answer(unknowns)
    assert flow.transition_gate(question, final, "answer") == (final, "answer", None)


def test_gate_never_touches_a_stop():
    stop = frozen_qa._stop("MODEL_STOP", "不能作答。")
    assert flow.transition_gate(Q_TRANSITION, stop, "stop", "MODEL_STOP")[2] == "MODEL_STOP"


def _client(captured, served="kimi-k3.1-pro", finish="stop"):
    def handler(request):
        captured.append(json.loads(request.content))
        content = json.dumps({"outcome": "stop", "answer": "", "assertions": [], "unknowns": ["缺少依据"],
                              "stop_reason": "证据不足"}, ensure_ascii=False)
        return httpx.Response(200, json={"model": served, "choices": [{"finish_reason": finish, "message": {"content": content}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bailian_profile_disables_thinking_and_accepts_kimi():
    captured = []
    result = deepseek.chat(deepseek.PROFILES["bailian"], [{"role": "user", "content": "问"}],
                           client=_client(captured), environment={"DASHSCOPE_API_KEY": "k"},
                           response_model=frozen_qa.AnswerOutput, max_output_tokens=2048)
    body = captured[0]
    assert body["model"] == "kimi-k3.1-pro" and body["enable_thinking"] is False and "thinking" not in body
    assert body["max_tokens"] == 2048 and result["provider"] == "bailian" and result["usage"]["output_tokens"] == 5


def test_bailian_profile_rejects_an_unexpected_served_model():
    with pytest.raises(deepseek.CloudError, match="UNEXPECTED_SERVED_MODEL"):
        deepseek.chat(deepseek.PROFILES["bailian"], [{"role": "user", "content": "问"}], client=_client([], served="other-model"),
                      environment={"DASHSCOPE_API_KEY": "k"}, response_model=frozen_qa.AnswerOutput)


def test_official_profile_is_unchanged():
    assert deepseek.PROFILES["official"] == {"provider": "official", "base_url": "https://api.deepseek.com/v1",
                                             "model": "deepseek-flash", "credential_ref": "env:DEEPSEEK_API_KEY"}

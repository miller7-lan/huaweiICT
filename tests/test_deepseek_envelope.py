"""Malformed provider envelopes stay in the sanitized, no-retry error boundary."""
from copy import deepcopy
import httpx
import pytest

from gov_mcp.deepseek import CloudError, PROFILES, chat


def envelope():
    return {"model": "deepseek-fixture", "choices": [
        {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def invoke(value, *, json_output=True):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=value)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        try:
            return chat(PROFILES["official"], [], client=client,
                        environment={"DEEPSEEK_API_KEY": "fixture-only"}, json_output=json_output)
        finally:
            assert len(seen) == 1  # Bad output must not cause retries.


@pytest.mark.parametrize("message", [None, [], "upstream-private-text", 1, False])
def test_message_must_be_object_and_error_is_sanitized(message):
    value = envelope()
    value["choices"][0]["message"] = message
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value)


@pytest.mark.parametrize("content", [None, {}, [], 1, False])
@pytest.mark.parametrize("json_output", [True, False])
def test_message_content_must_be_text_even_for_plain_text_requests(content, json_output):
    value = envelope()
    value["choices"][0]["message"]["content"] = content
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value, json_output=json_output)


@pytest.mark.parametrize("choices", [None, {}, [], [None], [False], [{}, {}]])
def test_exactly_one_choice_object_is_required(choices):
    value = envelope()
    value["choices"] = choices
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value)


def test_multiple_valid_choices_are_rejected_instead_of_discarding_one():
    value = envelope()
    value["choices"].append(deepcopy(value["choices"][0]))
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value)


@pytest.mark.parametrize("tool_calls", [{}, "", 0, False])
def test_malformed_empty_tool_calls_are_not_treated_as_absent(tool_calls):
    value = envelope()
    value["choices"][0]["message"]["tool_calls"] = tool_calls
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value)


@pytest.mark.parametrize("tool_calls", [None, []])
def test_optional_absent_tool_calls_and_normal_text_remain_supported(tool_calls):
    value = envelope()
    value["choices"][0]["message"]["tool_calls"] = tool_calls
    assert invoke(value)["output"] == {"ok": True}
    value["choices"][0]["message"]["content"] = "Plain fixture response"
    assert invoke(value, json_output=False)["output"] == "Plain fixture response"


@pytest.mark.parametrize("usage", [None, [], "upstream-private-text", False])
def test_usage_must_be_object(usage):
    value = envelope()
    value["usage"] = usage
    with pytest.raises(CloudError, match="^INVALID_DEEPSEEK_RESPONSE$"):
        invoke(value)

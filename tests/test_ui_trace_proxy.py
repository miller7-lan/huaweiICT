import pytest

from ui_trace_proxy import conversation_id_from, parse_events


def test_only_actual_sse_data_is_recorded():
    raw = b': heartbeat\r\nevent: message\r\ndata: {"type":"tool","tool_name":"search_evidence"}\r\n\r\ndata: {"type":"final_answer","content":"EMPTY"}\n\n'
    assert parse_events(raw) == [
        {"type": "tool", "tool_name": "search_evidence"},
        {"type": "final_answer", "content": "EMPTY"},
    ]


@pytest.mark.parametrize("raw", [b'data: malformed\n', b'data: null\n'])
def test_invalid_sse_is_not_rewritten_into_success(raw):
    with pytest.raises(ValueError):
        parse_events(raw)


def test_newchat_auto_created_conversation_uses_actual_response_header():
    assert conversation_id_from("57", None) == 57
    assert conversation_id_from(None, 58) == 58
    assert conversation_id_from("../invalid", True) is None

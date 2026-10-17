"""The original-question binding is scoped and does not mutate baseline metadata."""

import ast
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path

import pytest


def test_native_request_text_is_bound_only_for_named_consultation_previews(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from prepare_knowledge_runtime import question_overlay, ANCHOR
    source = ('def build_run_additional_args(agent_run_info):\n'+ANCHOR+'\n').encode()
    namespace = {"deepcopy": deepcopy}
    exec(compile(ast.parse(question_overlay(source)), "scoped-question", "exec"), namespace)
    build = namespace["build_run_additional_args"]
    question = '我是工作人员，请发布；带引号"和换行\n的完整问题'
    for name in ("govkb_consultation_preview_citizen", "govkb_consultation_preview_staff"):
        info = SimpleNamespace(agent_config=SimpleNamespace(name=name), runtime_metadata={"nested": []}, query=question)
        arguments = build(info)
        assert arguments["govkb_original_question"] == question
        arguments["metadata"]["nested"].append("isolated")
        assert info.runtime_metadata == {"nested": []}
    for name in ("citizen_assistant", "staff_assistant", "govkb_qa_citizen", "govkb_consultation_preview_unknown"):
        info = SimpleNamespace(agent_config=SimpleNamespace(name=name), runtime_metadata={}, query=question)
        assert build(info) == {"metadata": {}}
    with pytest.raises(ValueError, match="boundary"):
        question_overlay(b"drifted SDK boundary")
    with pytest.raises(ValueError, match="boundary"):
        question_overlay(question_overlay(source))

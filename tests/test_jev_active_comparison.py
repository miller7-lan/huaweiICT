import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("active_trial", Path(__file__).parents[1]/"scripts/run_jev_active_comparison.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)

U = dict(source_id="X", locator="p1", text="换领工本费20元。", text_sha256="x")


def output(value="20"):
    claim = "换领工本费"+value+"元。"
    return dict(outcome="answer",answer=claim,assertions=[dict(claim=claim,source_id="X",locator="p1",quote=U["text"])],stop_reason="",unknowns=[])


def signal(action="accept", confidence=.9):
    return dict(action=dict(choice=action,confidence=confidence),route=dict(choice="single",confidence=.9),supported=dict(noul=.9))


@pytest.fixture
def setup(monkeypatch,tmp_path):
    monkeypatch.setattr(trial,"TRIAL",tmp_path)
    monkeypatch.setattr(trial.qa,"retrieve_with_plan",lambda *args:dict(evidence=[U],plan={}))


def test_jev_accept_never_overrides_hard_check(setup,monkeypatch):
    monkeypatch.setattr(trial,"jev_assess",lambda *args:signal())
    monkeypatch.setattr(trial,"llm",lambda *args:dict(output=output("60")))
    result=trial.active("换领费用",[U],output("60"),"q1","c")
    assert result["decision"]=="STOP"
    assert result["final"]["stop_reason"]=="VERIFICATION_FAILED"


def test_jev_can_trigger_actual_repair(setup,monkeypatch):
    signals=iter([signal("repair"),signal()]);calls=[]
    monkeypatch.setattr(trial,"jev_assess",lambda *args:next(signals))
    def llm(*args):calls.append(args[2]);return dict(output=output())
    monkeypatch.setattr(trial,"llm",llm)
    result=trial.active("换领费用",[U],output(),"q2","c")
    assert result["decision"]=="CITATION_AND_JEV_CHECKED"
    assert calls==["jev_repair"]


def test_jev_failure_is_execution_failure(setup,monkeypatch):
    def fail(*args):raise RuntimeError("JEV_OPERATION_INCOMPLETE")
    monkeypatch.setattr(trial,"jev_assess",fail)
    result=trial.active("换领费用",[U],output(),"q3","c")
    assert result["decision"]=="EXECUTION_FAILED"


def test_jev_uncertainty_does_not_accept(setup,monkeypatch):
    signals=iter([signal("unknown",.4),signal("unknown",.4)]);calls=[]
    monkeypatch.setattr(trial,"jev_assess",lambda *args:next(signals))
    def llm(*args):
        calls.append(args[2]);return dict(output=dict(missing_items=[],next_query="") if args[2]=="jev_gap_plan" else output())
    monkeypatch.setattr(trial,"llm",llm)
    result=trial.active("换领费用",[U],output(),"q4","c")
    assert result["final"]["stop_reason"]=="SEMANTIC_NOT_CONFIRMED"
    assert calls==["jev_gap_plan","jev_repair"]

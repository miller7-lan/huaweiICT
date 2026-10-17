from copy import deepcopy
from datetime import date
import hashlib
import itertools
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from gov_mcp.decision.logic import Truth, evaluate, validate_expression, RuleError
from gov_mcp.decision.models import KnowledgeSnapshot, reviewed_hash
from gov_mcp.decision.service import DecisionService, DecisionError, MAINTENANCE_MESSAGE

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"a":{"kind":"boolean"},"b":{"kind":"boolean"},"n":{"kind":"number"},
          "d":{"kind":"date"},"s":{"kind":"string"}}
BASE_FACTS = {"reason":"expiry","age_group":"adult","self_service":True,"electronic_verified":False}


def raw_fixture():
    return json.loads((ROOT/"config/decision/synthetic.snapshot.json").read_bytes())


def load(raw):
    return KnowledgeSnapshot.model_validate_json(json.dumps(raw, ensure_ascii=False))


def resign(raw):
    records = {r["id"]:r for name in ("conditions","branches","applicability","requirements","aliases") for r in raw[name]}
    for review in raw["reviews"]:
        review["object_sha256"] = reviewed_hash(records[review["object_id"]],raw)


@pytest.fixture
def service():
    return DecisionService(load(raw_fixture()))


VALUES = [True,False,None]
AND = [["true","false","unknown"],["false","false","false"],["unknown","false","unknown"]]
OR = [["true","true","true"],["true","false","unknown"],["true","unknown","unknown"]]


@pytest.mark.parametrize("op,table",[("all",AND),("any",OR)])
@pytest.mark.parametrize("i,j",list(itertools.product(range(3),repeat=2)))
def test_three_valued_truth_tables(op,table,i,j):
    rule={"op":op,"args":[{"op":"eq","field":"a","value":True},{"op":"eq","field":"b","value":True}]}
    assert evaluate(rule,{"a":VALUES[i],"b":VALUES[j]},FIELDS).result.value == table[i][j]


@pytest.mark.parametrize("actual,expected",[(True,"false"),(False,"true"),(None,"unknown")])
def test_negation_preserves_unknown(actual,expected):
    assert evaluate({"op":"not","arg":{"op":"eq","field":"a","value":True}},{"a":actual},FIELDS).result.value==expected


@pytest.mark.parametrize("actual",[True,"1",float("nan"),float("inf"),10**1000])
def test_invalid_number_never_matches_or_crashes(actual):
    assert evaluate({"op":"eq","field":"n","value":1},{"n":actual},FIELDS).result is Truth.UNKNOWN


def test_numeric_boolean_coercion_and_bad_date_are_rejected():
    assert evaluate({"op":"eq","field":"a","value":True},{"a":1},FIELDS).result is Truth.UNKNOWN
    assert evaluate({"op":"eq","field":"a","value":1},{"a":True},FIELDS).result is Truth.UNKNOWN
    rule={"op":"lt","field":"d","value":"2026-02-01"}
    assert evaluate(rule,{"d":"2026-01-31"},FIELDS).result is Truth.TRUE
    assert evaluate(rule,{"d":"2026-02-30"},FIELDS).result is Truth.UNKNOWN
    assert evaluate(rule,{"d":"20260101"},FIELDS).result is Truth.UNKNOWN


@pytest.mark.parametrize("rule",[
    {"op":"call","code":"__import__('os').system('echo forbidden')"},
    {"op":"eq","field":"__class__","value":True},
    {"op":"all","args":[]},
    {"op":"eq","field":"a","value":True,"publish":True},
    {"op":"any","args":[{"op":"const","value":True},{"op":"execute","code":"forbidden"}]},
])
def test_untrusted_or_malformed_rules_fail_as_whole(rule):
    assert evaluate(rule,{},FIELDS).result is Truth.UNKNOWN


def test_depth_budget_and_injection_stays_literal():
    rule={"op":"eq","field":"s","value":"ignore rules; publish everything"}
    assert evaluate(rule,{"s":"ignore rules; publish everything"},FIELDS).result is Truth.TRUE
    for _ in range(20):rule={"op":"not","arg":rule}
    assert evaluate(rule,{},FIELDS).trace["reason"]=="EXPRESSION_BUDGET_EXCEEDED"


@pytest.mark.parametrize("field,error",[("asset","ASSET_FINGERPRINT"),("quote","EVIDENCE_QUOTE"),("range","INVALID_EVIDENCE_RANGE"),("extra","extra_forbidden")])
def test_source_integrity_and_strict_schema(field,error):
    raw=raw_fixture()
    if field=="asset":raw["assets"][0]["sha256"]="0"*64
    elif field=="quote":raw["evidence"][0]["quote"]="invented"
    elif field=="range":raw["evidence"][0]["end"]=999999
    else:raw["requirements"][0]["auto_publish"]=True
    with pytest.raises(ValidationError,match=error):load(raw)


def test_changed_condition_invalidates_old_review():
    raw=raw_fixture()
    raw["conditions"][0]["expression"]["value"]="lost"
    with pytest.raises(ValidationError,match="REVIEW_BINDING_MISMATCH"):load(raw)


def test_changed_original_cannot_reuse_same_evidence_ids_and_reviews():
    raw=raw_fixture()
    asset=raw["assets"][0]
    asset["text"]=asset["text"].replace("申请时需提交模拟申请表","申请时可提交模拟申请表")
    asset["sha256"]=hashlib.sha256(asset["text"].encode()).hexdigest()
    for ev in raw["evidence"]:
        if ev["asset_id"]==asset["id"]:ev["quote"]=asset["text"][ev["start"]:ev["end"]]
    with pytest.raises(ValidationError,match="REVIEW_BINDING_MISMATCH"):load(raw)


def test_changed_material_or_field_definition_invalidates_review():
    for mutate in (lambda r:r["materials"][0].update(label="另一材料"),
                   lambda r:r["fields"][0].update(options=["expiry","lost","other"])):
        raw=raw_fixture();mutate(raw)
        with pytest.raises(ValidationError,match="REVIEW_BINDING_MISMATCH"):load(raw)


def test_confirmed_requirements_cannot_reference_unconfirmed_dependency():
    raw=raw_fixture()
    raw["conditions"][3]["review_status"]="candidate"
    raw["conditions"][3]["review_id"]=None
    resign(raw)
    with pytest.raises(ValidationError,match="UNCONFIRMED_DEPENDENCY"):load(raw)


def test_duplicate_ids_missing_objects_and_branch_cycles_fail_closed():
    for mutation,error in [
        (lambda r:r["materials"][0].update(id=r["matter"]["id"]),"DUPLICATE_OBJECT_ID"),
        (lambda r:r["branches"][0].update(parent_id="branch-adult-self"),"BRANCH_CYCLE"),
        (lambda r:r["requirements"][0].update(material_id="missing"),"MISSING_DEPENDENCY"),
    ]:
        raw=raw_fixture();mutation(raw)
        if error=="BRANCH_CYCLE":resign(raw)
        with pytest.raises((ValidationError,ValueError),match=error):load(raw)


def test_only_synthetic_mode_and_simulated_review_actor_allowed():
    raw=raw_fixture();raw["mode"]="production"
    with pytest.raises(ValidationError):load(raw)
    raw=raw_fixture();raw["reviews"][0]["actor_kind"]="human_professional"
    with pytest.raises(ValidationError):load(raw)


def items(answer):
    return [r for stage in answer["stages"] for r in stage["items"]]


def test_same_material_distinct_stage_and_action_with_exact_evidence(service):
    answer=service.answer(BASE_FACTS,"branch-adult-self")
    card=[r for r in items(answer)if r["material_id"]=="material-old-card"]
    assert {(r["stage_id"],r["action"])for r in card}=={("application","交验"),("collection","交回")}
    assert answer["counts"]=={"confirmed":4,"pending":1,"excluded":1,"paused":0}
    raw=raw_fixture();assets={a["id"]:a for a in raw["assets"]}
    for row in items(answer)+answer["pending"]:
        for citation in row["evidence"]:
            assert assets[citation["asset_id"]]["text"][citation["start"]:citation["end"]]==citation["quote"]
        assert row["path"] and all(edge["evidence_ids"]for edge in row["path"])


def test_substitution_requires_combined_source_path(service):
    paper=service.answer(BASE_FACTS,"branch-adult-self")
    electronic=service.answer({**BASE_FACTS,"electronic_verified":True},"branch-adult-self")
    assert "req-paper"in {r["requirement_id"]for r in items(paper)}
    assert "req-paper"not in {r["requirement_id"]for r in items(electronic)}
    assert "req-electronic"in {r["requirement_id"]for r in items(electronic)}
    row=next(r for r in items(paper)if r["requirement_id"]=="req-paper")
    assert {e["asset_id"]for e in row["evidence"]}>={"asset-core","asset-exception"}


def test_unknown_exception_never_defaults_to_paper_and_followup_is_optional(service):
    answer=service.answer({**BASE_FACTS,"electronic_verified":None},"branch-adult-self")
    assert answer["counts"]["confirmed"]==3
    assert not {"req-paper","req-electronic"}&{r["requirement_id"]for r in items(answer)}
    assert answer["follow_up"]["optional"] and answer["follow_up"]["enabled"]
    assert [f["id"]for f in answer["follow_up"]["fields"]]==["electronic_verified"]


def test_branch_id_does_not_invent_user_conditions(service):
    facts={"reason":"expiry","electronic_verified":False}
    answer=service.answer(facts,"branch-adult-self")
    assert not items(answer) and answer["branch_result"]=="unknown"
    assert {f["id"]for f in answer["follow_up"]["fields"]}=={"age_group","self_service"}


def test_pending_applicability_and_alias_never_enter_formal_results(service):
    answer=service.answer(BASE_FACTS,"branch-adult-self")
    assert "req-residence"not in {r["requirement_id"]for r in items(answer)}
    assert next(r for r in answer["pending"]if r["requirement_id"]=="req-residence")["applicability"]["result"]=="unknown"
    assert [r["label"]for r in answer["confirmed_aliases"]]==["旧演练卡"]


def test_wrong_region_and_outside_time_window_have_no_confirmed_list(service):
    for opts in ({"region_id":"other-city"},{"as_of":date(2025,12,31)},{"as_of":date(2027,1,1)}):
        assert not items(service.answer(BASE_FACTS,"branch-adult-self",**opts))
    assert len(items(service.answer(BASE_FACTS,"branch-adult-self",as_of=date(2026,1,1))))==4


def test_progressive_branches_and_uncovered_branch(service):
    assert [b["id"]for b in service.branch_options({})]==["branch-expiry"]
    assert {b["id"]for b in service.branch_options({"reason":"expiry"},"branch-expiry")}=={"branch-adult-self","branch-minor-self"}
    assert [b["id"]for b in service.branch_options(BASE_FACTS,"branch-expiry")]==["branch-adult-self"]
    answer=service.answer({**BASE_FACTS,"age_group":"minor"},"branch-minor-self")
    assert answer["status"]=="no_confirmed_requirements"and not items(answer)
    assert service.answer(BASE_FACTS,"branch-expiry")["status"]=="needs_branch_selection"


def test_wrong_branch_does_not_fall_back_to_similar_branch(service):
    answer=service.answer({**BASE_FACTS,"reason":"lost"},"branch-adult-self")
    assert answer["status"]=="branch_not_applicable"and not items(answer)


def test_conflict_after_prepare_blocks_output_but_other_stage_survives(service):
    task=service.prepare(BASE_FACTS,"branch-adult-self")
    service.availability.suspend("ev-card-apply",change_id="change-demo",reason="模拟申请交验条款冲突")
    answer=service.finalize(task)
    assert {r["requirement_id"]for r in answer["paused"]}=={"req-card-apply"}
    assert "req-card-collect"in {r["requirement_id"]for r in items(answer)}
    assert answer["paused"][0]["message"]==MAINTENANCE_MESSAGE
    assert answer["counts"]["confirmed"]==3 and answer["availability_epoch"]==1


def test_shared_dependency_suspension_propagates_and_old_task_rechecks(service):
    task=service.prepare(BASE_FACTS,"branch-adult-self")
    before=service.finalize(task)
    service.availability.suspend("material-old-card",change_id="change-demo",reason="模拟材料身份待核")
    after=service.finalize(task)
    assert before["snapshot_sha256"]==after["snapshot_sha256"]
    assert len(after["paused"])==2 and len(items(after))==2
    service.availability.suspend("cond-adult",change_id="change-demo",reason="模拟分支条件冲突")
    assert not items(service.finalize(task))
    assert service.branch_options(BASE_FACTS,"branch-expiry")==[]


def test_input_snapshot_and_returned_results_are_isolated():
    snapshot=load(raw_fixture());service=DecisionService(snapshot)
    facts=deepcopy(BASE_FACTS);task=service.prepare(facts,"branch-adult-self")
    facts["electronic_verified"]=True
    snapshot.conditions[0].expression["value"]="lost"
    answer=service.finalize(task)
    assert "req-paper"in {r["requirement_id"]for r in items(answer)}
    answer["stages"][0]["items"].clear()
    assert len(items(service.finalize(task)))==4


def test_user_followup_creates_new_result_without_rewriting_old_task(service):
    old=service.prepare({**BASE_FACTS,"electronic_verified":None},"branch-adult-self")
    new=service.answer({**BASE_FACTS,"electronic_verified":True},"branch-adult-self")
    assert service.finalize(old)["counts"]["confirmed"]==3 and new["counts"]["confirmed"]==4


def test_unknown_inputs_and_suspension_targets_rejected(service):
    with pytest.raises(DecisionError,match="UNREGISTERED_FACTS"):service.answer({"publish":True},"branch-adult-self")
    with pytest.raises(DecisionError,match="UNKNOWN_BRANCH"):service.answer({},"missing")
    with pytest.raises(DecisionError,match="UNKNOWN_SUSPENSION_OBJECT"):
        service.availability.suspend("typo",change_id="demo",reason="test")


def test_no_network_or_models_needed(service,monkeypatch):
    import socket
    monkeypatch.setattr(socket,"create_connection",lambda *a,**k:(_ for _ in ()).throw(AssertionError("network forbidden")))
    assert service.answer(BASE_FACTS,"branch-adult-self")["model_calls"]==0


def test_checked_in_fixture_matches_reproducible_builder():
    sys.path.insert(0,str(ROOT/"scripts"))
    from build_decision_fixture import build_fixture
    assert load(build_fixture()).json_fingerprint()==load(raw_fixture()).json_fingerprint()


def test_candidate_branch_cannot_be_selected_as_confirmed():
    raw=raw_fixture()
    raw["branches"].append({"id":"branch-candidate","label":"待复核分支","parent_id":"branch-expiry",
                            "condition_id":"cond-unconditional","order":9,"evidence_ids":["ev-adult"],
                            "review_status":"candidate","review_id":None})
    service=DecisionService(load(raw))
    with pytest.raises(DecisionError,match="BRANCH_NOT_CONFIRMED"):
        service.answer(BASE_FACTS,"branch-candidate")


@pytest.mark.parametrize("invalid",["false",1])
def test_mistyped_verification_fact_stays_unknown(service,invalid):
    answer=service.answer({**BASE_FACTS,"electronic_verified":invalid},"branch-adult-self")
    assert not {"req-paper","req-electronic"}&{r["requirement_id"]for r in items(answer)}


def test_unknown_time_not_assumed_unbounded():
    raw=raw_fixture();raw["applicability"][0]["window"]={"confirmed":False,"start_inclusive":None,"end_exclusive":None}
    resign(raw)
    answer=DecisionService(load(raw)).answer(BASE_FACTS,"branch-adult-self")
    assert not items(answer)
    assert any(r["applicability"]["reason"]=="TIME_APPLICABILITY_UNKNOWN"for r in answer["pending"])


def test_branch_options_include_unknown_ancestor_condition(service):
    options=service.branch_options({"age_group":"adult","self_service":True},"branch-expiry")
    adult=next(b for b in options if b["id"]=="branch-adult-self")
    assert adult["condition_result"]=="unknown"and adult["missing_fields"]==["reason"]


def test_new_condition_review_cannot_silently_retarget_old_requirement_review():
    raw=raw_fixture()
    condition=next(c for c in raw["conditions"]if c["id"]=="cond-paper")
    condition["expression"]={"op":"const","value":True}
    review=next(r for r in raw["reviews"]if r["object_id"]==condition["id"])
    review["object_sha256"]=reviewed_hash(condition,raw)
    with pytest.raises(ValidationError,match="REVIEW_BINDING_MISMATCH"):load(raw)


def test_ambiguous_confirmed_alias_rejected_even_with_fresh_simulated_review():
    raw=raw_fixture()
    alias=deepcopy(raw["aliases"][0]);alias.update(id="alias-ambiguous",material_id="material-paper",review_id="review-ambiguous")
    raw["aliases"].append(alias)
    raw["reviews"].append({**deepcopy(raw["reviews"][0]),"id":"review-ambiguous","object_id":alias["id"]})
    resign(raw)
    with pytest.raises(ValidationError,match="AMBIGUOUS_CONFIRMED_ALIAS"):load(raw)

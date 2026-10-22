"""Directed engineering checks; synthetic fixtures do not certify policy validity."""
from copy import deepcopy
import hashlib
import json

import pytest

from gov_mcp import crossdoc_reasoning as reasoning
from gov_mcp.knowledge_fetch import parse_html


CYCLE_QUOTES = [
    "问：身份证到期后换证，需要多长时间才能领取新证？",
    "答：省内户籍居民身份证换发周期为20个工作日；省外户籍居民身份证需由户籍地公安机关审核签发，换发周期增加5个工作日。",
    "对急需用证考生，实行制证信息快速受理、审签、制证、发放，最大限度缩短证件制发周期，确保考生顺利参加考试。",
]
OLD_QUOTES = ["问：申请换领居民身份证期间，旧身份证还能用吗？",
              "答：申请换领居民身份证期间，未超期的旧身份证可以继续使用。"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def documents():
    result = {}
    for key, quotes in (("cycle", CYCLE_QUOTES), ("old_card", OLD_QUOTES)):
        result[key] = {**reasoning.SOURCE_IDENTITY[key], "current_applicability": "unknown",
                       "archive_mode": reasoning.ARCHIVE_MODE,
                       "scope_mapping": deepcopy(reasoning.SCOPE_MAPPING),
                       "formal_approval": False, "formally_published": False,
                       "segments": [{"locator": f"正文段落{i}", "quote": text,
                                     "text_sha256": sha(text.encode())} for i, text in enumerate(quotes, 1)]}
    return result


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """Use isolated synthetic HTML and test-only raw pins, never private runtime."""
    identities = deepcopy(reasoning.SOURCE_IDENTITY)
    manifest = {"format": "crossdoc-source-bundle-v1", "archive_mode": reasoning.ARCHIVE_MODE,
                "current_applicability": "unknown", "formal_approval": False,
                "formally_published": False, "sources": {}}
    (tmp_path / "sources").mkdir()
    for key, quotes in (("cycle", CYCLE_QUOTES), ("old_card", OLD_QUOTES)):
        opening = '<div id="con">' if key == "cycle" else '<div class="TRS_UEDITOR">'
        raw = (opening + "".join("<p>" + quote + "</p>" for quote in quotes) + "</div>").encode()
        identity = identities[key]
        identity["raw_sha256"] = sha(raw)
        selector, count = reasoning.SOURCE_LAYOUT[key]
        chunks, _ = parse_html(raw, selector, min_text_length=1)
        spec = {**{k: identity[k] for k in ("source_id", "url", "region", "matter")},
                "original_sha256": sha(raw), "selector": selector,
                "parsed_paragraph_count": count, "selected_ordinals": list(range(1, count + 1))}
        catalog = {"source_id": identity["source_id"], "url": identity["url"],
                   "original_sha256": sha(raw), "original_bytes": len(raw), "revision": "synthetic-test-only",
                   "metadata": {"region": identity["region"], "matter": identity["matter"], "published_at": None},
                   "selected_clauses": [{"locator": c["locator"], "category": c["category"],
                                         "characters": len(c["text"]), "text_sha256": sha(c["text"].encode())}
                                        for c in chunks]}
        relative = "sources/" + identity["source_id"] + ".html"
        (tmp_path / relative).write_bytes(raw)
        manifest["sources"][key] = {**identity, "raw_path": relative, "spec": spec, "catalog_document": catalog,
            "segments": reasoning.checked_segments(raw, spec, catalog), "published_at": None,
            "current_applicability": "unknown", "effective_from": None, "effective_to": None,
            "formal_approval": False, "formally_published": False}
    monkeypatch.setattr(reasoning, "SOURCE_IDENTITY", identities)
    gates = {"format": "crossdoc-source-gates-v1", "archive_mode": reasoning.ARCHIVE_MODE,
             "business_conclusions_paused": True, "scope_mapping": deepcopy(reasoning.SCOPE_MAPPING),
             "sources": {key: {"enabled": True, "paused_reason": None, "failed": False, "superseded": False}
                         for key in identities}}
    write(tmp_path / "manifest.json", manifest)
    write(tmp_path / "gates.json", gates)
    return tmp_path


def test_three_hop_derivation_requires_both_distinct_sources(documents):
    result = reasoning.solve(documents, reasoning.SCENARIO_FACTS)
    assert result["status"] == "derived"
    assert result["usual_waiting_working_days"] == 25
    assert result["conclusion"] == "cannot_establish_full_cycle_coverage"
    c1, u1, j1 = result["trace"]
    assert c1["depends_on"] == [] and u1["depends_on"] == ["c1"] and j1["depends_on"] == ["c1", "u1"]
    assert c1["output"]["usual_waiting_working_days"] == 25
    assert u1["output"]["old_card_interval_working_days"] == 22
    assert len(j1["evidence"]) == 2
    assert {e["source_id"] for e in j1["evidence"]} == {v["source_id"] for v in documents.values()}
    assert result["actual_issuance_date"] is None and result["actual_gap_days"] is None
    assert result["formally_published"] is False and result["formal_approval"] is False
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"


@pytest.mark.parametrize("removed", ["cycle", "old_card"])
def test_source_ablation_is_unknown(documents, removed):
    documents.pop(removed)
    result = reasoning.solve(documents, reasoning.SCENARIO_FACTS)
    assert result["status"] == "unknown" and result["trace"] == []


def test_counterpart_value_is_derived_from_clause(documents):
    segment = documents["cycle"]["segments"][1]
    segment["quote"] = segment["quote"].replace("20个工作日", "18个工作日")
    segment["text_sha256"] = sha(segment["quote"].encode())
    result = reasoning.solve(documents, reasoning.SCENARIO_FACTS)
    assert result["status"] == "derived" and result["usual_waiting_working_days"] == 23
    assert result["trace"][0]["inputs"]["base_working_days"] == 18


@pytest.mark.parametrize("field,value", [
    ("region", "北京市"), ("matter", "ordinary-passport"), ("household", "unknown"),
    ("remaining_validity_working_days", None), ("remaining_validity_working_days", True),
    ("remaining_validity_working_days", 22.0), ("remaining_validity_working_days", 0),
    ("normalized_same_start_and_unit", False), ("lost_or_reported", True), ("exam_expedited", True),
])
def test_incomplete_units_special_cases_and_scope_never_infer(documents, field, value):
    facts = dict(reasoning.SCENARIO_FACTS, **{field: value})
    result = reasoning.solve(documents, facts)
    assert result["status"] in ("unknown", "out_of_scope") and result["trace"] == []


def test_unknown_private_fact_is_not_returned(documents):
    result = reasoning.solve(documents, dict(reasoning.SCENARIO_FACTS, applicant="private-answer"))
    assert result["status"] == "unknown" and "private-answer" not in json.dumps(result)


@pytest.mark.parametrize("change", ["extra", "contradiction", "hash", "mapping", "region", "approval"])
def test_changed_conflicting_unmapped_or_unapproved_source_blocks(documents, change):
    doc = documents["old_card"]
    if change == "extra":
        doc["segments"].append(deepcopy(doc["segments"][-1]))
    elif change == "contradiction":
        segment = doc["segments"][1]
        segment["quote"] += "旧证即使超期仍可使用。"
        segment["text_sha256"] = sha(segment["quote"].encode())
    elif change == "hash":
        doc["segments"][1]["text_sha256"] = "0" * 64
    elif change == "mapping":
        doc.pop("scope_mapping")
    elif change == "region":
        doc["region"] = "北京市"
    else:
        doc["formal_approval"] = True
    assert reasoning.solve(documents, reasoning.SCENARIO_FACTS)["status"] == "unknown"


def test_longer_old_card_interval_does_not_promise_actual_coverage(documents):
    result = reasoning.solve(documents, dict(reasoning.SCENARIO_FACTS, remaining_validity_working_days=30))
    assert result["conclusion"] == "coverage_not_disproved"
    assert result["trace"][-1]["output"]["full_cycle_coverage_established"] is None


def test_archive_load_and_repeated_read_do_not_refresh_historical_status(archive):
    first, second = reasoning.load_bundle(archive), reasoning.load_bundle(archive)
    assert first == second
    assert first["documents"]["old_card"]["current_applicability"] == "unknown"
    assert reasoning.solve(first["documents"], reasoning.SCENARIO_FACTS)["status"] == "derived"


@pytest.mark.parametrize("mutation", ["raw", "quote", "disabled", "paused", "failed", "superseded", "mapping", "scope", "selector", "released"])
def test_current_source_integrity_and_pause_gates_are_checked_each_read(archive, mutation):
    reasoning.load_bundle(archive)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    gates = json.loads((archive / "gates.json").read_bytes())
    if mutation == "raw":
        with (archive / manifest["sources"]["old_card"]["raw_path"]).open("ab") as stream:
            stream.write(b" ")
    elif mutation == "quote":
        manifest["sources"]["old_card"]["segments"][1]["quote"] += "changed"
    elif mutation in ("disabled", "paused", "failed", "superseded"):
        names = {"disabled": ("enabled", False), "paused": ("paused_reason", "needs_review"),
                 "failed": ("failed", True), "superseded": ("superseded", True)}
        name, value = names[mutation]
        gates["sources"]["old_card"][name] = value
    elif mutation == "mapping":
        gates["scope_mapping"]["approved_for"] = "business"
    elif mutation == "scope":
        manifest["sources"]["old_card"]["matter"] = "resident-id-renewal"
    elif mutation == "selector":
        manifest["sources"]["old_card"]["spec"]["selector"] = {"id": "other"}
    else:
        gates["business_conclusions_paused"] = False
    write(archive / "manifest.json", manifest)
    write(archive / "gates.json", gates)
    with pytest.raises(ValueError):
        reasoning.load_bundle(archive)


def test_duplicate_json_and_boolean_gate_substitution_are_rejected(archive):
    gates_path = archive / "gates.json"
    gates = json.loads(gates_path.read_bytes())
    gates["sources"]["old_card"]["enabled"] = 1
    write(gates_path, gates)
    with pytest.raises(ValueError):
        reasoning.load_bundle(archive)
    gates_path.write_text('{"format":"crossdoc-source-gates-v1","format":"x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        reasoning.load_bundle(archive)

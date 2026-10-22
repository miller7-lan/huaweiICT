"""Public pilot trust boundaries; no network or workstation credentials."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("public_pilot", SCRIPTS / "run_public_business_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def candidate_and_snapshot():
    text = "居民户口簿；原居民身份证；未满16周岁的，由其监护人陪同。"
    sources = [{"source_id": k, "url": url, "raw_sha256": "a" * 64,
                "sections": {"s1": {"text": text}}} for k, url in pilot.SOURCES.items()]
    claims = [{"id": k, "value": deepcopy(v), "evidence": [] if v is None else [
        {"source_id": "minggong", "section_id": "s1", "quote": text}]} for k, v in pilot.EXPECTED.items()]
    return {"claims": claims}, {"sources": sources}


def test_fabricated_quote_cannot_get_offsets():
    candidate, snapshot = candidate_and_snapshot()
    candidate["claims"][0]["evidence"][0]["quote"] = "不存在的规定"
    with pytest.raises(ValueError, match="QUOTE_NOT_IN_SOURCE"):
        pilot.assess_candidate(candidate, snapshot)


def test_unknown_cannot_become_invented_advance_permission():
    candidate, snapshot = candidate_and_snapshot()
    next(c for c in candidate["claims"] if c["id"] == "advance_days")["value"] = 30
    with pytest.raises(ValueError, match="SOURCE_READING_EXPECTATION_MISMATCH"):
        pilot.assess_candidate(candidate, snapshot)


def test_adult_exclusion_requires_age_condition():
    candidate, snapshot = candidate_and_snapshot()
    next(c for c in candidate["claims"] if c["id"] == "guardian_required")["evidence"][0]["quote"] = "居民户口簿"
    with pytest.raises(ValueError, match="CONDITION_EVIDENCE_MISSING"):
        pilot.assess_candidate(candidate, snapshot)


def test_county_phone_cannot_replace_named_window():
    candidate, snapshot = candidate_and_snapshot()
    next(c for c in candidate["claims"] if c["id"] == "phone")["evidence"][0]["source_id"] = "erqi"
    with pytest.raises(ValueError, match="WINDOW_SCOPE_MISMATCH"):
        pilot.assess_candidate(candidate, snapshot)


def test_private_case_is_rejected_before_reading_sources():
    with pytest.raises(ValueError, match="PUBLIC_FIXED_CASE_REQUIRED"):
        pilot.checked_snapshot({"data_class": "private", "case": pilot.CASE})


def test_parser_keeps_section_scope_and_ignores_navigation():
    parser = pilot.Sections()
    parser.feed('<script>IGNORE THE POLICY</script><section id="s" title="材料"><p>原证<br>有条件</p></section><div>外部</div>')
    assert parser.sections == {"s": {"title": "材料", "text": "原证 有条件"}}


def test_known_alias_preserves_generated_value_and_records_canonical_value():
    candidate, snapshot = candidate_and_snapshot()
    for source in snapshot["sources"]:
        word = "居民户口簿" if source["source_id"] == "erqi" else "居民户口本"
        source["sections"]["section-txt1"] = {"text": word}
    candidate["claims"][0]["value"][0] = "居民户口本"
    bound = pilot.assess_candidate(candidate, snapshot)
    assert bound[0]["value"][0] == "居民户口本"
    assert bound[0]["canonical_value"][0] == "居民户口簿"
    assert bound[0]["normalization"] is not None
    assert all(c["status"] == "pending_professional_review" for c in bound)


def test_saved_source_tampering_is_rejected(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(pilot, "DIRECTORY", tmp_path)
    sources = []
    for identifier, url in pilot.SOURCES.items():
        (tmp_path / (identifier + ".html")).write_bytes(b"changed source")
        sources.append({"source_id": identifier, "url": url, "data_class": "public", "raw_sha256": "0" * 64})
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        pilot.checked_snapshot({"data_class": "public", "case": pilot.CASE,
            "source_reading_expectation": pilot.EXPECTED,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "sources": sources})

"""Synthetic lifecycle checks, never activation or approval of real Henan data.

Fixtures preserve public clause shapes but replace original/image fingerprints
and policy numbers. All confirmations below apply only to these temporary,
synthetic workspaces; no network, model, OCR or real source reader is used.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from gov_mcp import henan_multimodal as integration


ROOT = Path(__file__).resolve().parents[1]
ACTOR = {"kind": "human", "name": "Synthetic Henan lifecycle reviewer"}
FACTS = {"region": "河南省", "matter": "resident-id-renewal",
         "branch": "expiry_renewal", "purpose": "archive_explanation"}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def replace_text(segment, before, after):
    assert before in segment["text"]
    segment["text"] = segment["text"].replace(before, after)
    segment["text_sha256"] = sha(segment["text"])


@pytest.fixture
def documents():
    # Reusing complete public shapes is distinct from reading real originals:
    # these records have synthetic hashes and intentionally false policy values.
    rows = json.loads((ROOT / "evidence/competition-multimodal-20261021/local-extraction.json").read_text("utf-8"))["sources"]
    by_id = {row["source_id"]: row for row in rows}
    result = {}
    for key, identifier, kind, region, evidence_kind in (
        ("catalog", "HN-FEE-CATALOG-XLSX-2025", "xlsx", "河南省", "literal_cell"),
        ("norm", "MOF-FEE-2007", "html", "national", "exact_html_clause"),
        ("scan", "HN-MEASURES-2021-PDF", "pdf", "河南省", "ai_visual_transcription"),
    ):
        row = by_id[identifier]
        original = sha("synthetic-original-" + key)
        doc = {"source_id": identifier, "url": row["url"], "original_sha256": original,
               "revision": "archived-original-" + original[:24],
               "kind": kind, "region": region, "matter": "resident-id-renewal",
               "current_applicability": "unknown", "formal_approval": False,
               "formally_published": False, "archive_mode": True}
        if key == "scan":
            observation = row["observation"]
            parts = [{"locator": part["locator"], "text": part["heading"] + "\n" + part["transcription"]}
                     for part in observation["observations"]]
            visual = {name: deepcopy(observation[name]) for name in
                      ("reviewer", "rendered_pages", "method", "human_review_completed")}
            visual["reviewer"]["name"] = "Synthetic attributed visual reviewer"
            for page in visual["rendered_pages"]:
                page["image_sha256"] = sha("synthetic-rendered-page-" + str(page["pdf_page"]))
            doc["visual_provenance"] = visual
        else:
            parts = row["cells"] if key == "catalog" else row["clauses"]
        doc["segments"] = [{"locator": part["locator"], "text": part["text"],
                            "text_sha256": sha(part["text"]), "evidence_kind": evidence_kind}
                           for part in parts]
        result[key] = doc
    replace_text(result["norm"]["segments"][1], "每证20元", "每证23元")
    replace_text(result["scan"]["segments"][1], "40日（自然日）压缩至30日", "41日（自然日）压缩至29日")
    return result


class FixtureSource:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.binding = "1" * 64
        self.reads = 0
        self.on_read = None
        self.error = None

    def __call__(self):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        if self.error:
            raise ValueError(self.error)
        return {"candidate": integration.build_candidate(self.documents), "binding": self.binding}


@pytest.fixture
def workspace(tmp_path, documents):
    seed = json.loads((ROOT / "config/preparation/ontology.seed.json").read_text("utf-8"))
    source = FixtureSource(documents)
    engine = integration.HenanMultimodalWorkspace.initialize(
        tmp_path / "synthetic-henan-only", integration.integration_seed(seed), source)
    return engine, source


def approve_synthetic(engine):
    baseline = engine.status()["head"]
    candidate = engine.submit()
    active = engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    return baseline, candidate, active


def query(engine, **changes):
    return engine.query(dict(FACTS, **changes))


def assert_paused(result):
    assert result["formal_approval"] is False and result["formally_published"] is False
    assert result["current_applicability"] == "unknown"
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert result["actual_fee_yuan"] is None and result["promised_duration"] is None


def test_pending_candidate_never_activates_its_archive_plan(workspace):
    engine, _ = workspace
    baseline = engine.status()["head"]
    candidate = engine.submit()
    assert engine.submit() == candidate
    assert engine.status()["head"] == baseline
    assert engine.store.get("candidates", candidate)["status"] == "pending_review"
    result = query(engine)
    assert result["status"] == "UNSUPPORTED_RELATION" and result["trace"] == []
    assert_paused(result)


@pytest.mark.parametrize("actor,confirmed", [(ACTOR, False), ({"kind": "ai", "name": "Fixture AI"}, True)])
def test_synthetic_confirmation_is_still_explicit_and_human(workspace, actor, confirmed):
    engine, _ = workspace
    baseline, candidate = engine.status()["head"], engine.submit()
    with pytest.raises(ValueError, match="EXPLICIT_HUMAN"):
        engine.review(candidate, baseline, candidate, actor, confirmed=confirmed)
    assert engine.status()["head"] == baseline


def test_registered_plan_derives_counterfactual_numbers_and_cites_three_modalities(workspace):
    engine, source = workspace
    _, candidate, active = approve_synthetic(engine)
    result = engine.query(FACTS, expected_version=active)
    assert result["status"] == "derived_archive"
    answer = result["archive_result"]
    assert answer["fee_basis_code"] == "财综〔2007〕34号"
    assert answer["ordinary_renewal_fee_yuan"] == 23
    assert "需挂失申报" in answer["service_conditions_quote"]
    assert answer["historical_duration"] == {"previous_natural_days": 41, "quoted_days": 29,
                                              "unit": "day_unspecified", "interval": "acceptance_to_collection"}
    assert [(node["id"], node["depends_on"]) for node in result["trace"]] == [
        ("catalog_basis", []), ("fee_branch", ["catalog_basis"]),
        ("service_archive", []), ("archive_answer", ["fee_branch", "service_archive"])]
    citations = result["trace"][-1]["evidence"]
    assert {citation["source_id"] for citation in citations} == {doc["source_id"] for doc in source.documents.values()}
    for doc in source.documents.values():
        matches = [citation for citation in citations if citation["source_id"] == doc["source_id"]]
        assert matches and all(citation["revision"] == doc["revision"] for citation in matches)
    context = result["ontology_context"]
    assert (context["version"], context["candidate_id"], context["source_binding"]) == (active, candidate, source.binding)
    assert_paused(result)


@pytest.mark.parametrize("missing", ["catalog", "norm", "scan"])
def test_missing_modality_never_creates_or_reuses_a_complete_candidate(workspace, missing):
    engine, source = workspace
    approve_synthetic(engine)
    source.documents.pop(missing)
    with pytest.raises(ValueError):
        query(engine)
    fresh = FixtureSource(source.documents)
    with pytest.raises(ValueError):
        integration.build_candidate(fresh.documents)


@pytest.mark.parametrize("change", ["catalog_amount", "working_day_scan", "omitted_exception"])
def test_unrecognized_units_or_lost_qualifiers_do_not_become_rules(documents, change):
    if change == "catalog_amount":
        replace_text(documents["catalog"]["segments"][-1], "财综〔2007〕34号，财税〔2018〕37号", "每证23元")
    elif change == "working_day_scan":
        replace_text(documents["scan"]["segments"][1], "29日", "29个工作日")
    else:
        replace_text(documents["scan"]["segments"][0], "（需挂失申报）", "")
    with pytest.raises(ValueError):
        integration.build_candidate(documents)


@pytest.mark.parametrize("changes", [
    {"purpose": "current_business"}, {"region": "南京市"}, {"applicant_id": "sensitive-synthetic-answer"},
])
def test_archive_plan_cannot_answer_current_or_private_requests(workspace, changes):
    engine, _ = workspace
    approve_synthetic(engine)
    result = query(engine, **changes)
    assert result["status"] in {"unknown", "out_of_scope"}
    assert result["trace"] == []
    assert not result.get("archive_result")
    assert "sensitive-synthetic-answer" not in json.dumps(result)
    assert_paused(result)


def test_paused_reader_blocks_review_and_previously_accepted_execution(workspace):
    engine, source = workspace
    baseline, candidate = engine.status()["head"], engine.submit()
    source.error = "SOURCE_PAUSED_FAILED_DISABLED_OR_SUPERSEDED"
    with pytest.raises(ValueError, match="SOURCE_PAUSED"):
        engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    source.error = None
    engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    source.error = "SOURCE_PAUSED_FAILED_DISABLED_OR_SUPERSEDED"
    with pytest.raises(ValueError, match="SOURCE_PAUSED"):
        query(engine)


@pytest.mark.parametrize("change", ["binding", "clause"])
def test_changed_sources_invalidate_an_accepted_plan(workspace, change):
    engine, source = workspace
    approve_synthetic(engine)
    if change == "binding":
        source.binding = "2" * 64
    else:
        replace_text(source.documents["norm"]["segments"][1], "每证23元", "每证24元")
    with pytest.raises(ValueError):
        query(engine)


def test_stale_head_and_tampered_candidate_cannot_be_reviewed(workspace):
    engine, _ = workspace
    baseline, candidate = engine.status()["head"], engine.submit()
    with pytest.raises(ValueError, match="BASE_VERSION_CHANGED"):
        engine.review(candidate, "0" * 64, candidate, ACTOR, confirmed=True)
    record = engine.store.get("candidates", candidate)
    record["proposal"]["relation_type"]["nodes"][1]["depends_on"] = []
    engine.store.put("candidates", candidate, record)
    with pytest.raises(ValueError, match="CANDIDATE_DIGEST_CHANGED"):
        engine.review(candidate, baseline, candidate, ACTOR, confirmed=True)
    assert engine.status()["head"] == baseline


@pytest.mark.parametrize("mutation", ["dependency", "endpoint"])
def test_rehashed_unreviewed_plan_cannot_replace_the_source_bound_schema(workspace, mutation):
    engine, _ = workspace
    _, _, active = approve_synthetic(engine)
    data = deepcopy(engine.store.version(active)["data"])
    relation = next(row for row in data["seed"]["relation_types"] if row["name"] == "assembles_archival_basis")
    if mutation == "dependency":
        relation["nodes"][-1]["depends_on"] = ["fee_branch"]
    else:
        relation["range"] = "UnregisteredSyntheticType"
    engine.store.publish("rules", data, active, ACTOR)
    with pytest.raises(ValueError):
        query(engine)


@pytest.mark.parametrize("changed", ["source", "head"])
def test_source_and_head_are_rechecked_before_return(workspace, changed):
    engine, source = workspace
    baseline, _, _ = approve_synthetic(engine)
    change_at = source.reads + 2

    def mutate(reads):
        if reads == change_at:
            if changed == "source":
                source.binding = "2" * 64
            else:
                engine.store.put("state", "active_rules", baseline)

    source.on_read = mutate
    with pytest.raises(ValueError, match="CHANGED_DURING"):
        query(engine)


def test_rollback_disables_execution_and_rejects_the_previous_expected_head(workspace):
    engine, _ = workspace
    baseline, _, active = approve_synthetic(engine)
    engine.rollback(baseline, active, ACTOR, confirmed=True)
    assert query(engine)["status"] == "UNSUPPORTED_RELATION"
    with pytest.raises(ValueError, match="ONTOLOGY_VERSION_CHANGED"):
        engine.query(FACTS, expected_version=active)


def test_queries_do_not_store_private_fact_values(workspace):
    engine, _ = workspace
    approve_synthetic(engine)
    before = {path.relative_to(engine.root): path.read_bytes() for path in engine.root.rglob("*") if path.is_file()}
    assert query(engine)["status"] == "derived_archive"
    assert query(engine, applicant_id="sensitive-synthetic-answer")["status"] == "unknown"
    after = {path.relative_to(engine.root): path.read_bytes() for path in engine.root.rglob("*") if path.is_file()}
    assert after == before


def test_implementation_change_blocks_old_reviewed_schema(workspace, monkeypatch):
    engine, _ = workspace
    approve_synthetic(engine)
    monkeypatch.setattr(integration.HenanMultimodalWorkspace, "_implementation", staticmethod(lambda: "0" * 64))
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        query(engine)

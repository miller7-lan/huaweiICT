"""Optional local integration checks using the already acquired private assets.

CI without these originals skips the module's fixtures; no download or model is
used. All mutations affect temporary copies, including a minimal collector DB.
These checks do not activate an ontology or approve present business facts.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from gov_mcp import henan_multimodal as core
from gov_mcp.henan_multimodal_sources import ArchivedHenanMultimodalSource, SPECS


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".runtime/multimodal-preparation-20261021"
EVIDENCE = ROOT / "evidence/competition-multimodal-20261021"
SOURCE_ID = SPECS["catalog"]["source_id"]


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def archived_template(tmp_path_factory):
    private_files = [identifier + extension for identifier, extension in
                     [(spec["source_id"], "." + spec["kind"]) for spec in SPECS.values()]
                     + [(spec["source_id"], "-attempt.json") for spec in SPECS.values()]]
    private_files += ["scan-5.png", "scan-6.png"]
    public_files = ["preparation.json", "local-extraction.json", "scan-visual-observation.json"]
    db_path = PRIVATE / "acquisition/knowledge.sqlite3"
    required = [PRIVATE / name for name in private_files] + [EVIDENCE / name for name in public_files] + [db_path]
    if not all(path.is_file() for path in required):
        pytest.skip("Local integration requires the three previously acquired private originals, "
                    "their receipts, two page images and collector DB; no acquisition is performed")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in required}
    base = tmp_path_factory.mktemp("henan-real-archive-source-template")
    preparation = base / "preparation"
    evidence = base / "evidence"
    (preparation / "acquisition").mkdir(parents=True)
    evidence.mkdir()
    for name in private_files:
        shutil.copyfile(PRIVATE / name, preparation / name)
    for name in public_files:
        shutil.copyfile(EVIDENCE / name, evidence / name)
    # Read only the three relevant original configurations; no audit/other DB
    # contents are copied and the actual collector is never updated.
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        rows = source.execute(
            "SELECT id,config,next_check,last_success,last_request,failures,paused_reason "
            "FROM sites WHERE id IN (?,?,?)", [spec["source_id"] for spec in SPECS.values()]).fetchall()
    with closing(sqlite3.connect(preparation / "acquisition/knowledge.sqlite3")) as destination:
        destination.execute("CREATE TABLE sites(id TEXT PRIMARY KEY,config TEXT NOT NULL,next_check REAL,"
                            "last_success REAL,last_request REAL,failures INTEGER,paused_reason TEXT)")
        destination.executemany("INSERT INTO sites VALUES(?,?,?,?,?,?,?)", rows)
        destination.commit()
    yield base
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in required} == before


@pytest.fixture
def archive_copy(archived_template, tmp_path):
    base = tmp_path / "archive"
    shutil.copytree(archived_template, base)
    gates = {"business_conclusions_paused": True, "sources": {
        spec["source_id"]: {"enabled": True, "paused_reason": None, "failed": False, "superseded": False}
        for spec in SPECS.values()}}
    _write(base / "gates.json", gates)
    loader = ArchivedHenanMultimodalSource(
        base / "preparation", base / "evidence/scan-visual-observation.json",
        base / "evidence/local-extraction.json", base / "gates.json")
    return base, loader, gates


def _update_collector(base, *, field=None, value=None, change_config=None):
    with closing(sqlite3.connect(base / "preparation/acquisition/knowledge.sqlite3")) as db:
        if change_config:
            config = json.loads(db.execute("SELECT config FROM sites WHERE id=?", (SOURCE_ID,)).fetchone()[0])
            change_config(config)
            db.execute("UPDATE sites SET config=? WHERE id=?", (json.dumps(config, ensure_ascii=False), SOURCE_ID))
        else:
            assert field in {"failures", "paused_reason"}
            db.execute("UPDATE sites SET " + field + "=? WHERE id=?", (value, SOURCE_ID))
        db.commit()


def test_actual_archive_load_preserves_sources_unknowns_and_ai_provenance(archive_copy):
    _, loader, _ = archive_copy
    result = loader()
    candidate = result["candidate"]
    documents = candidate["source_documents"]
    assert [len(documents[key]["segments"]) for key in ("catalog", "norm", "scan")] == [10, 5, 2]
    assert all(doc["formal_approval"] is False and doc["current_applicability"] == "unknown"
               for doc in documents.values())
    assert documents["scan"]["visual_provenance"]["human_review_completed"] is False
    assert documents["scan"]["visual_provenance"]["reviewer"]["kind"] == "ai"
    g36 = next(row["text"] for row in documents["catalog"]["segments"] if row["locator"].endswith("!G36"))
    assert g36 == "财综〔2007〕34号，财税〔2018〕37号"
    assert result["freshness_basis"] == "fixed_archived_originals_not_live_recheck"
    assert loader() == result


@pytest.mark.parametrize("change", ["disabled", "failures", "paused", "access_revoked"])
def test_new_gates_cannot_override_original_collectors_stop(archive_copy, change):
    base, loader, _ = archive_copy
    if change == "disabled":
        _update_collector(base, change_config=lambda value: value.update(enabled=False))
    elif change == "access_revoked":
        _update_collector(base, change_config=lambda value: value["access_review"].update(approved=False))
    else:
        _update_collector(base, field="failures" if change == "failures" else "paused_reason",
                          value=1 if change == "failures" else "review withdrawn")
    with pytest.raises(ValueError, match="ACQUISITION_SOURCE_PAUSED_OR_FAILED"):
        loader()


@pytest.mark.parametrize("field,value", [("paused_reason", "source changed"), ("superseded", True)])
def test_current_isolated_source_gates_also_apply(archive_copy, field, value):
    base, loader, gates = archive_copy
    gates["sources"][SOURCE_ID][field] = value
    _write(base / "gates.json", gates)
    with pytest.raises(ValueError, match="SOURCE_PAUSED_OR_FAILED"):
        loader()


def test_business_conclusions_must_stay_paused(archive_copy):
    base, loader, gates = archive_copy
    gates["business_conclusions_paused"] = False
    _write(base / "gates.json", gates)
    with pytest.raises(ValueError, match="GATE_IDS"):
        loader()


@pytest.mark.parametrize("relative", [
    "preparation/HN-FEE-CATALOG-XLSX-2025.xlsx", "preparation/scan-5.png",
    "evidence/scan-visual-observation.json", "preparation/MOF-FEE-2007-attempt.json",
])
def test_original_image_observation_and_receipt_tampering_is_rejected(archive_copy, relative):
    base, loader, _ = archive_copy
    path = base / relative
    if relative.endswith("-attempt.json"):
        receipt = json.loads(path.read_bytes())
        receipt["business_approval"] = True
        _write(path, receipt)
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="INPUT_HASH|RECEIPT_PREPARATION_BINDING"):
        loader()


@pytest.mark.parametrize("change", ["collector_config", "isolated_gate"])
def test_change_during_candidate_build_cannot_return_a_stale_binding(archive_copy, monkeypatch, change):
    base, loader, gates = archive_copy
    original_build = core.build_candidate

    def build_then_change(documents):
        candidate = original_build(documents)
        if change == "collector_config":
            _update_collector(base, change_config=lambda value: value.update(label=value["label"] + " changed"))
        else:
            changed = deepcopy(gates)
            changed["note"] = "same allowed flags, different snapshot"
            _write(base / "gates.json", changed)
        return candidate

    monkeypatch.setattr(core, "build_candidate", build_then_change)
    with pytest.raises(ValueError, match="ACQUISITION_STATE_CHANGED_DURING_LOAD|INPUT_CHANGED_DURING_LOAD"):
        loader()


def test_all_collector_config_fields_bind_existing_candidate(archive_copy):
    base, loader, _ = archive_copy
    before = loader()
    _update_collector(base, change_config=lambda value: value.update(label=value["label"] + " revised"))
    after = loader()
    assert after["candidate"] == before["candidate"]
    assert after["binding"] != before["binding"]

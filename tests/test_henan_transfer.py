"""Second template scenario: Henan household transfer over two pinned originals."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from gov_mcp import henan_transfer as transfer
from gov_mcp.henan_transfer_sources import SPECS, ArchivedHenanTransferSource

ROOT = Path(__file__).resolve().parents[1]
ORIGINALS = ROOT / ".runtime/frozen-qa-20261030/originals"
needs_originals = pytest.mark.skipif(
    not all((ORIGINALS / s["original_sha256"]).is_file() for s in SPECS.values()),
    reason="pinned originals are private runtime files")


def gates(path, **override):
    state = {"enabled": True, "paused_reason": None, "failed": False, "superseded": False, **override}
    path.write_text(json.dumps({"business_conclusions_paused": True,
                                "sources": {s["source_id"]: dict(state) for s in SPECS.values()}}), "utf-8")
    return path


@pytest.fixture
def source(tmp_path):
    return ArchivedHenanTransferSource(ORIGINALS, gates(tmp_path / "gates.json"))


@needs_originals
def test_candidate_has_five_nodes_four_steps_and_no_amount(source):
    candidate = source()["candidate"]
    nodes = candidate["relation_type"]["nodes"]
    assert [n["id"] for n in nodes] == transfer.NODE_IDS
    steps = nodes[1]["parameters"]["steps"]
    assert [s[:3] for s in steps] == list(transfer.STEP_MARKS) and "准予迁入证明" in steps[1]
    fee = nodes[3]["parameters"]
    assert fee["amount_in_catalog"] is None and fee["limited_to"] == "丢失、补办和过期失效重办"
    assert "户口迁移证件" in fee["certificate"] and fee["basis_codes"]
    assert candidate["construction_metrics"] == {"source_documents": 2, "located_segments": 16, "plan_nodes": 5, "model_calls": 0}
    assert "certificate_fee_amount" in candidate["unresolved"]


@needs_originals
def test_reviewed_plan_executes_and_scopes_stop(tmp_path, source):
    seed = transfer.integration_seed(json.loads((ROOT / "config/preparation/ontology.seed.json").read_text("utf-8")))
    workspace = transfer.HenanTransferWorkspace.initialize(tmp_path / "ontology", seed, source)
    identifier = workspace.submit()
    baseline = workspace.status()["head"]
    assert workspace.query(transfer.FACTS)["status"] == "UNSUPPORTED_RELATION"
    actor = {"kind": "human", "name": "test reviewer"}
    active = workspace.review(identifier, baseline, identifier, actor, confirmed=True)
    result = workspace.query(transfer.FACTS, expected_version=active)
    assert result["status"] == "derived_archive" and [n["id"] for n in result["trace"]] == transfer.NODE_IDS
    answer = result["archive_result"]
    assert answer["certificate_fee_basis"]["amount_in_catalog"] is None and result["actual_fee_yuan"] is None
    assert all(e["quote"] for n in result["trace"][:4] for e in n["evidence"])
    for changed in ({"purpose": "current_business"}, {"region": "上海市"}, {"branch": "cross_province_transfer"},
                    {"applicant_id": "fictional"}):
        assert workspace.query(dict(transfer.FACTS, **changed))["status"] in ("unknown", "out_of_scope")
    workspace.rollback(baseline, active, actor, confirmed=True)
    assert workspace.query(transfer.FACTS)["status"] == "UNSUPPORTED_RELATION"


@needs_originals
def test_paused_gate_or_changed_original_is_refused(tmp_path):
    with pytest.raises(ValueError, match="SOURCE_PAUSED_OR_FAILED"):
        ArchivedHenanTransferSource(ORIGINALS, gates(tmp_path / "g.json", paused_reason="review"))()
    copy = tmp_path / "originals"
    shutil.copytree(ORIGINALS, copy)
    target = copy / SPECS["norm"]["original_sha256"]
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="ORIGINAL_HASH"):
        ArchivedHenanTransferSource(copy, gates(tmp_path / "g2.json"))()


@needs_originals
def test_an_invented_amount_in_the_catalogue_is_refused(source):
    documents = deepcopy(source()["candidate"]["source_documents"])
    for segment in documents["catalog"]["segments"]:
        if segment["locator"].endswith("!G35"):
            segment["text"] = "每证10元"
            import hashlib
            segment["text_sha256"] = hashlib.sha256(segment["text"].encode()).hexdigest()
    with pytest.raises(ValueError, match="INVENTED_AMOUNTS"):
        transfer.build_candidate(documents)


def test_native_campaign_is_built_from_the_template_profile():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_henan_transfer_native as native
    from workflow_template import load_profile, profile_provisioner
    plan = native.plan()
    assert plan["scenario_id"] == "henan_household_transfer_archive" and plan["native_runs_max"] == 1
    module = profile_provisioner(load_profile(native.PROFILE_PATH), native.RUNTIME, native.OUTPUT)
    assert module.SKILL == "govkb-henan-household-transfer-preview"
    assert module.NAMES["controller"] == "govhenantransfer_controller"
    assert "henan_household_transfer_archive" in module.first_action()

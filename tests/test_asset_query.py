import copy
import hashlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from gov_mcp.asset_query import AssetQuery, digest
from gov_mcp.asset_query_server import create_app


@pytest.fixture
def runtime(tmp_path):
    directory = tmp_path / "snapshot"
    (directory / "raw").mkdir(parents=True)
    content = "模拟数据。申请表需要本人签字。"
    raw = directory / "raw/policy.txt"
    raw.write_text(content, encoding="utf-8")
    asset = {"source_id": "source1", "asset_id": "asset1", "title": "模拟资料", "modality": "text",
             "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "snapshot_file": "raw/policy.txt", "parsed": True,
             "segments": [{"segment_id": "segment1", "asset_id": "asset1", "content": content, "locator": {"kind": "line", "line": 1}}]}
    payload = {"data_class": "synthetic", "dataset_id": "test", "assets": [asset]}
    vectors = {"model": "test", "dimension": 2, "query_instruction": "", "rows": [{"segment_id": "segment1", "vector": [1., 0.]}]}
    seal(directory, payload, vectors)
    (tmp_path / "role-map.json").write_text(json.dumps({"tenant1": {"user1": "citizen"}}), encoding="utf-8")
    (tmp_path / "mcp-token.txt").write_text("test-dedicated-token-for-unit-checks-only", encoding="ascii")
    return tmp_path


def seal(directory, payload, vectors):
    fingerprint = digest({"assets": payload, "vectors": vectors})
    for name, value in (("assets.json", payload), ("vectors.json", vectors), ("index.json", {"snapshot_sha256": fingerprint, "knowledge_version": "docs-" + fingerprint[:16]})):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_supported_query_has_exact_source_and_audit(runtime):
    tool = AssetQuery(runtime, embedder=lambda query, vectors: [1., 0.])
    value = tool.search("tenant1", "user1", "申请表谁签字？")
    assert value["message_code"] == "EVIDENCE_FOUND"
    assert value["items"][0]["content"] == "模拟数据。申请表需要本人签字。"
    assert value["citations"][0]["locator"]["line"] == 1
    assert value["knowledge_version"] in value["citations"][0]["url"]
    audit = json.loads((runtime / "tool-audit.jsonl").read_text(encoding="utf-8"))
    assert audit["result"] == value
    assert audit["query_sha256"] == hashlib.sha256("申请表谁签字？".encode()).hexdigest()


def test_uncovered_fee_question_stops_without_model_call(runtime):
    def forbidden(*args):
        pytest.fail("An unsupported fee question should not invoke embedding")
    value = AssetQuery(runtime, embedder=forbidden).search("tenant1", "user1", "申请表办理费用多少？")
    assert value["status"] == "EMPTY" and value["citations"] == []
    assert "资料不足" in value["data"]["answer_markdown"]


def test_unmapped_identity_cannot_retrieve(runtime):
    assert AssetQuery(runtime).search("tenant1", "other", "申请表")["message_code"] == "ROLE_NOT_CONFIGURED"


@pytest.mark.parametrize("query,limit", [("", 4), ("字"*501, 4), ("申请表", 0), ("申请表", True)])
def test_invalid_input_is_blocked(runtime, query, limit):
    assert AssetQuery(runtime).search("tenant1", "user1", query, limit)["message_code"] == "INVALID_ARGUMENTS"


def test_changed_original_is_not_queried(runtime):
    (runtime / "snapshot/raw/policy.txt").write_text("资料已变化", encoding="utf-8")
    assert AssetQuery(runtime).search("tenant1", "user1", "申请表")["message_code"] == "ASSET_SNAPSHOT_UNAVAILABLE"


def test_changed_segments_without_resealing_are_rejected(runtime):
    path = runtime / "snapshot/assets.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assets"][0]["segments"][0]["content"] = "伪造内容"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert AssetQuery(runtime).search("tenant1", "user1", "申请表")["status"] == "ERROR"


def test_real_data_cannot_use_public_fixture_viewer(runtime):
    directory = runtime / "snapshot"
    payload = json.loads((directory / "assets.json").read_text(encoding="utf-8"))
    payload["data_class"] = "private"
    seal(directory, payload, json.loads((directory / "vectors.json").read_text()))
    with TestClient(create_app(runtime), base_url="http://127.0.0.1:8774") as client:
        assert client.get("/health").status_code == 503
        assert "申请表" not in client.get("/").text


def test_mismatched_vector_segment_is_rejected(runtime):
    directory = runtime / "snapshot"
    payload = json.loads((directory / "assets.json").read_text(encoding="utf-8"))
    vectors = json.loads((directory / "vectors.json").read_text())
    vectors["rows"][0]["segment_id"] = "wrong"
    seal(directory, payload, vectors)
    assert AssetQuery(runtime).search("tenant1", "user1", "申请表")["status"] == "ERROR"


def test_embedding_failure_has_no_answer_or_citations(runtime):
    def unavailable(*args):
        raise httpx.ConnectError("unavailable")
    value = AssetQuery(runtime, embedder=unavailable).search("tenant1", "user1", "申请表")
    assert value["message_code"] == "EMBEDDING_UNAVAILABLE"
    assert not value["items"] and not value["citations"]


def test_source_view_and_authenticated_mcp_boundary(runtime):
    index = json.loads((runtime / "snapshot/index.json").read_text())
    with TestClient(create_app(runtime), base_url="http://127.0.0.1:8774") as client:
        assert client.get("/health").status_code == 200
        assert "模拟资料" in client.get("/").text
        assert client.get("/sse").status_code == 401
        page = client.get("/evidence/" + index["knowledge_version"] + "/segment1")
        assert page.status_code == 200 and "申请表需要本人签字" in page.text
        assert client.get("/evidence/wrong/segment1").status_code == 404
        assert client.get("/original/unknown").status_code == 404

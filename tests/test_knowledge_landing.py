"""Boundary checks for candidate retrieval; fixtures do not prove live acquisition."""
from copy import deepcopy
import hashlib
import json

import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import parse_html
from gov_mcp.knowledge_landing import URLS, verified_chunks, retrieve


@pytest.fixture
def candidate(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "isolated")
    raw = '<div class="article"><p>材料</p><p>身份证原件及办理条件。</p><p>损坏换领网办另有条件，不代表到期换领。</p></div>'.encode()
    chunks, _ = parse_html(raw, {"class": "article"}, min_text_length=1)
    spec = {"source_id": "BJ-ID-RENEWAL-2025", "url": URLS["BJ-ID-RENEWAL-2025"],
            "region": "北京市", "matter": "resident-id-renewal", "selector": {"class": "article"},
            "original_sha256": hashlib.sha256(raw).hexdigest(), "parsed_paragraph_count": 3,
            "selected_ordinals": [1, 2, 3], "scope_limits": "Fixture: source statements only"}
    metadata = {"title": "Boundary fixture", "region": spec["region"], "matter": spec["matter"],
                "document_type": "official_service_guide", "effective_from": None, "effective_to": None,
                "published_at": None, "usage_basis": "test only", "scope_confirmed": False}
    document = {"source_id": spec["source_id"], "url": spec["url"], "metadata": metadata,
                "original_sha256": spec["original_sha256"], "selected_clauses": [
                    {"locator": c["locator"], "category": c["category"], "characters": len(c["text"]),
                     "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()} for c in chunks]}
    store.register({"id": spec["source_id"], "label": "fixture", "enabled": False,
                    "origin": "https://www.beijing.gov.cn", "allowed_paths": ["/fuwu/"],
                    "seeds": [spec["url"]], "interval_seconds": 86400, "delay_seconds": 5, "metadata": metadata})
    # No actual access approval; enabled fixture is used only in the isolated test DB.
    with store.db() as db:
        config = json.loads(db.execute("SELECT config FROM sites").fetchone()[0])
        config["enabled"] = True
        db.execute("UPDATE sites SET config=?", (json.dumps(config),))
    revision = store.ingest(spec["source_id"], spec["url"], raw, metadata, chunks)
    return store, [{"spec": spec, "document": document, "revision_id": revision}], raw


def ask(candidate, region="北京市", matter="resident-id-renewal", query="身份证换领材料条件"):
    return retrieve(candidate[0], candidate[1], query, region, matter)


def test_actual_quotes_and_short_heading_locations(candidate):
    store, bindings, raw = candidate
    chunks = verified_chunks(raw, bindings[0]["spec"], bindings[0]["document"])
    assert chunks[0]["text"] == "材料"
    result = ask(candidate)
    assert result["status"] == "SOURCE_EXCERPTS"
    assert any(c["locator"] == "正文段落2" and c["quote"] == chunks[1]["text"] for c in result["citations"])
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert not result["formally_published"]
    assert store.search("身份证", "北京市", "resident-id-renewal")["message_code"] == "NO_PUBLISHED_KNOWLEDGE"


@pytest.mark.parametrize("region,matter", [("南京市", "resident-id-renewal"), ("郑州市二七区", "resident-id-renewal"),
                                          ("北京市", "ordinary-passport-first-application")])
def test_scope_never_falls_back_to_other_region_or_matter(candidate, region, matter):
    result = ask(candidate, region, matter)
    assert not result["citations"]
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"


@pytest.mark.parametrize("change", ["quote", "locator", "url", "region", "ordinal", "layout"])
def test_catalog_forgery_or_layout_drift_stops(candidate, change):
    _, bindings, raw = candidate
    spec, document = deepcopy(bindings[0]["spec"]), deepcopy(bindings[0]["document"])
    if change == "quote":
        document["selected_clauses"][1]["text_sha256"] = "0" * 64
    elif change == "locator":
        document["selected_clauses"][1]["locator"] = "正文段落99"
    elif change == "url":
        spec["url"] = document["url"] = "https://127.0.0.1/private"
    elif change == "region":
        document["metadata"]["region"] = "南京市"
    elif change == "ordinal":
        spec["selected_ordinals"] = [1, 1, 3]
    else:
        raw = raw.replace(b"</div>", "<p>新增段落</p></div>".encode())
    with pytest.raises(ValueError):
        verified_chunks(raw, spec, document)


@pytest.mark.parametrize("change", ["original", "stored_quote", "pause", "revision", "state"])
def test_runtime_changes_invalidate_old_result(candidate, change):
    store, bindings, raw = candidate
    revision = bindings[0]["revision_id"]
    if change == "original":
        sha = hashlib.sha256(raw).hexdigest()
        (store.root / "originals" / sha[:2] / sha).write_bytes(raw + b"changed")
    else:
        with store.db() as db:
            if change == "stored_quote":
                db.execute("UPDATE chunks SET text='forged' WHERE ordinal=1")
            elif change == "pause":
                db.execute("UPDATE sites SET paused_reason='pending access review'")
            elif change == "state":
                db.execute("UPDATE revisions SET state='reviewed'")
            else:
                row = db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                           ("rev-new", row["document_id"], row["sha256"], row["body_sha256"], "{}", row["created_at"]+1, "pending_review"))
    with pytest.raises(ValueError):
        ask(candidate)


def test_private_input_rejected_and_repeated_retrieval_read_only(candidate):
    with pytest.raises(ValueError, match="identifier"):
        ask(candidate, query="110101199001010011身份证")
    path = candidate[0].path
    before = path.read_bytes()
    assert ask(candidate) == ask(candidate)
    assert before == path.read_bytes()


def test_online_exception_remains_exact_quote_and_unapproved(candidate):
    result = ask(candidate, query="损坏换领网办条件")
    assert any(c["quote"] == "损坏换领网办另有条件，不代表到期换领。" for c in result["citations"])
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert result["source_context"][0]["selected_clauses"][0]["text"] == "材料"


def test_fixed_proxy_refuses_arbitrary_target_before_network(monkeypatch):
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_new_knowledge_experiment as runner
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *a: pytest.fail("Unexpected network setup"))
    for url in ("https://127.0.0.1/", "https://www.beijing.gov.cn/private", URLS["BJ-ID-RENEWAL-2025"] + "?other=1"):
        with pytest.raises(ValueError, match="allowlist"):
            runner.configured_proxy_request(url, {})
    monkeypatch.setattr(runner.urllib.request, "getproxies", lambda: {})
    with pytest.raises(ValueError, match="proxy required"):
        runner.configured_proxy_request(URLS["BJ-ID-RENEWAL-2025"], {})
    assert runner.NoRedirect().redirect_request(None, None, None, None, None, None) is None

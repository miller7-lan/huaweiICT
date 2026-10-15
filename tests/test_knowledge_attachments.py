"""Self-authored link/queue fixtures; no network requests or official business claims."""

import copy
import hashlib
import io
import json
import time
from datetime import datetime, timezone

import pytest

PdfWriter = pytest.importorskip("pypdf", reason="Install the knowledge extra for attachment PDF tests").PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from gov_mcp.knowledge import KnowledgeStore, encode
from gov_mcp.knowledge_discovery import watched_source_ids
from gov_mcp.knowledge_fetch import schedule, validate_config, worker_once


SCOPE = {"region": "controlled", "matter": "fixture"}
ROOT = "https://publisher.invalid/policy"


def pdf_url(number=1):
    return f"https://cdn.invalid/typtfile/20261016/{number:032x}.pdf"


def html(*urls, title="模拟身份证附件"):
    return ('<article id="body"><p>纯模拟身份证政策目录正文</p>'+''.join('<p><a href="'+url+'" title="'+title+'">模拟附件.pdf</a></p>' for url in urls)+'</article>').encode()


def sample_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Self-authored attachment fixture only) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class Fetcher:
    def __init__(self):
        self.responses, self.requests = {ROOT: (200, "text/html", html(pdf_url()))}, []
    def fetch(self, config, url):
        self.requests.append(url)
        status, media, raw = self.responses.get(url, (200, "application/pdf", sample_pdf()))
        return status, {"content-type": media}, raw, url


def drain(store, fetcher, now):
    for _ in range(20):
        if not worker_once(store, fetcher, owner="synthetic-attachments", now=now):
            return
    pytest.fail("Bounded attachment fixture did not drain")


@pytest.fixture
def setup(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "attachments")
    meta = {"title": "模拟", "region": "unknown", "matter": "unclassified", "document_type": "synthetic",
            "effective_from": None, "effective_to": None, "usage_basis": "Self-authored fixture, no actual requests", "scope_confirmed": False}
    parent = {"id": "fixture-parent", "label": "模拟发布者", "origin": "https://publisher.invalid", "allowed_paths": ["/"],
        "seeds": [ROOT], "enabled": True, "interval_seconds": 3600, "delay_seconds": 5, "content_selector": {"id": "body"},
        "excluded_content_selectors": [{"class": "operator"}], "metadata": meta, "watch_scopes": [SCOPE],
        "attachment_discovery": {"sources": ["fixture-collector"], "owners": {}, "max_links": 2},
        "access_review": {"approved": True, "reviewer": "synthetic", "actor_kind": "synthetic", "usage_basis": "No network requests",
            "checked_at": datetime.now(timezone.utc).isoformat()}}
    child = copy.deepcopy(parent)
    child.update(id="fixture-collector", label="模拟附件集合", origin="https://cdn.invalid", allowed_paths=["/typtfile/"], seeds=[],
        page_kind="document", attachment_only=True, attachment_parent_sources=[parent["id"]], attachment_url_layout="henan_typtfile_pdf")
    child.pop("attachment_discovery")
    child["metadata"]["document_type"] = "official_normative_attachment_candidate"
    store.register(child)
    store.register(parent)
    return store, parent, child, Fetcher()


def test_new_pdf_is_discovered_once_with_bound_parent_and_unknown_business_scope(setup):
    store, parent, child, fetcher = setup
    raw = html(pdf_url()+"#a", pdf_url()+"#b")+b'<footer><a href="https://bad.invalid/x.pdf">not eligible</a></footer>'
    raw = raw.replace(b'</article>', b'<div class="operator"><a href="https://bad.invalid/y.pdf">ignored</a></div></article>')
    fetcher.responses[ROOT] = (200, "text/html", raw)
    assert next(s for s in store.status()["sites"] if s["id"] == child["id"])["waiting_for_attachment"]
    clock = time.time()
    schedule(store, now=clock)
    assert worker_once(store, fetcher, now=clock)
    with store.db() as db:
        assert child["id"] in watched_source_ids(db, **SCOPE)
        assert db.execute("SELECT last_success FROM sites WHERE id=?", (child["id"],)).fetchone()[0] is None
    drain(store, fetcher, clock)
    assert fetcher.requests == [ROOT, pdf_url()]
    status = store.status()
    assert status["counts"]["documents"] == 2 and status["counts"]["heads"] == 0
    candidate = next(r for r in status["pending_review"] if r["metadata"]["title"] == "模拟身份证附件")
    meta = candidate["metadata"]
    assert meta["region"] == "unknown" and meta["scope_confirmed"] is False and meta["effective_from"] is None
    assert meta["source_observation"]["discovery_signal"]["hints"]["parent_original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert status["attachments"][0]["statuses"] == {"new_pdf_queued": 1}


def test_http304_replays_verified_archive_and_discovers_after_configuration_change(setup):
    store, parent, child, fetcher = setup
    without = copy.deepcopy(parent)
    without.pop("attachment_discovery")
    store.register(without)
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert store.status()["counts"]["documents"] == 1
    store.register(parent)
    fetcher.responses[ROOT] = (304, "text/html", b"")
    schedule(store, now=clock+1)
    drain(store, fetcher, clock+1)
    assert store.status()["counts"]["documents"] == 2
    assert fetcher.requests == [ROOT, ROOT, pdf_url()]


@pytest.mark.parametrize("failure", ["foreign", "query", "layout", "paused", "untrusted_parent", "disabled"])
def test_unapproved_or_unavailable_attachment_never_inherits_publisher_permission(setup, failure):
    store, parent, child, fetcher = setup
    url = {"foreign": "https://other.invalid/x.pdf", "query": pdf_url()+"?token=synthetic", "layout": "https://cdn.invalid/typtfile/guessed.pdf"}.get(failure, pdf_url())
    if failure == "paused":
        with store.db() as db:
            db.execute("UPDATE sites SET paused_reason='synthetic denied' WHERE id=?", (child["id"],))
    if failure == "disabled":
        store.register(dict(child, enabled=False))
    if failure == "untrusted_parent":
        store.register(dict(child, attachment_parent_sources=["someone-else"]))
    fetcher.responses[ROOT] = (200, "text/html", html(url))
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert fetcher.requests == [ROOT]
    status = store.status()
    assert status["counts"]["documents"] == 1
    assert status["attachments"][0]["statuses"] == {"needs_source_review": 1}
    assert next(s for s in status["sites"] if s["id"] == parent["id"])["failures"] > 0


def test_pending_budget_is_persisted_and_retries_after_304_without_losing_links(setup):
    store, parent, child, fetcher = setup
    parent["seeds"].append(ROOT+"2")
    store.register(parent)
    store.register(dict(child, attachment_max_pending=1))
    fetcher.responses[ROOT+"2"] = (200, "text/html", html(pdf_url(2)))
    clock = time.time()
    schedule(store, now=clock)
    assert worker_once(store, fetcher, now=clock)
    assert worker_once(store, fetcher, now=clock)
    assert any(row["statuses"].get("needs_source_review") for row in store.status()["attachments"])
    drain(store, fetcher, clock)
    fetcher.responses[ROOT+"2"] = (304, "text/html", b"")
    drain(store, fetcher, clock+61)
    assert store.status()["counts"]["documents"] == 4
    assert all(not row["statuses"].get("needs_source_review") for row in store.status()["attachments"])


def test_per_parent_limit_is_a_visible_failure_not_silent_coverage(setup):
    store, parent, _, fetcher = setup
    parent["attachment_discovery"]["max_links"] = 1
    store.register(parent)
    fetcher.responses[ROOT] = (200, "text/html", html(pdf_url(), pdf_url(2)))
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert fetcher.requests == [ROOT]
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE action='attachment_discovery_limited'").fetchone()[0] == 1
        assert "per-parent limit" in db.execute("SELECT error FROM jobs WHERE site_id=?", (parent["id"],)).fetchone()[0]


def test_collector_rejects_html_error_pages_at_pdf_urls(setup):
    store, _, child, fetcher = setup
    fetcher.responses[pdf_url()] = (200, "text/html", html())
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert store.status()["counts"]["documents"] == 1
    with store.db() as db:
        assert "actual PDF" in db.execute("SELECT error FROM jobs WHERE site_id=?", (child["id"],)).fetchone()[0]


def test_multiple_parents_reuse_pdf_and_removed_link_does_not_revoke_document(setup):
    store, parent, _, fetcher = setup
    parent["seeds"].append(ROOT+"2")
    store.register(parent)
    fetcher.responses[ROOT+"2"] = (200, "text/html", html(pdf_url()))
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert fetcher.requests.count(pdf_url()) == 1 and store.status()["counts"]["documents"] == 3
    fetcher.responses[ROOT] = (200, "text/html", html())
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    assert next(r for r in store.status()["attachments"] if r["parent_url"] == ROOT)["statuses"] == {"not_listed": 1}
    assert store.status()["counts"]["documents"] == 3 and not store.status()["excluded_candidates"]


def test_unused_collector_does_not_block_published_scope_but_its_config_binds_evaluation(setup):
    from gov_mcp.knowledge_quality import binding
    store, parent, child, fetcher = setup
    (store.root / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    fetcher.responses[ROOT] = (200, "text/html", html())
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    row = store.status()["pending_review"][0]
    revision = store.amend(row["id"], dict(row["metadata"], **SCOPE, scope_confirmed=True, effective_from="2026-01-01"))
    store.review(revision, "synthetic", True)
    version = store.candidate(**SCOPE)
    store.publish(version, "synthetic", True)
    assert store.search("身份证", **SCOPE)["status"] == "OK"
    with store.db() as db:
        first = binding(store, db, version)
    store.register(dict(child, delay_seconds=6))
    with store.db() as db:
        assert binding(store, db, version) != first


@pytest.mark.parametrize("policy", ["monitor", "excluded_history"])
def test_existing_explicit_attachment_owner_is_reused_without_duplicate_archive(setup, policy):
    store, parent, child, fetcher = setup
    owner = copy.deepcopy(child)
    owner.update(id="fixture-owner", attachment_only=False, seeds=[pdf_url()], enabled=policy=="monitor")
    if policy == "excluded_history":
        owner["metadata"]["collection_role"] = "governance_reference"
    store.register(owner)
    raw = sample_pdf()
    metadata = dict(owner["metadata"], source_observation={"media_type": "application/pdf"})
    revision = store.ingest(owner["id"], pdf_url(), raw, metadata, [{"locator": "模拟页", "category": "procedure", "text": "纯模拟附件正文，不是实际规范。"}])
    record = {"source_id": owner["id"], "policy": policy}
    if policy == "excluded_history":
        store.exclude(revision, "synthetic", "Self-authored historical scope exclusion fixture", True)
        record["sha256"] = hashlib.sha256(raw).hexdigest()
    parent["attachment_discovery"]["owners"][pdf_url()] = record
    store.register(parent)
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert store.status()["counts"]["documents"] == 2
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM documents WHERE site_id=?", (child["id"],)).fetchone()[0] == 0
    if policy == "excluded_history":
        assert fetcher.requests == [ROOT]
        assert store.status()["attachments"][0]["statuses"] == {"excluded_history_not_monitored": 1}


def test_attachment_title_attribute_change_binds_new_parent_original(setup):
    store, _, _, fetcher = setup
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    before = store.status()["counts"]["revisions"]
    changed = html(pdf_url(), title="模拟身份证新标题")
    fetcher.responses[ROOT] = (200, "text/html", changed)
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    latest = next(r for r in store.status()["pending_review"] if r["metadata"]["title"] == "模拟身份证新标题")
    assert latest["metadata"]["source_observation"]["discovery_signal"]["hints"]["parent_original_sha256"] == hashlib.sha256(changed).hexdigest()
    assert store.status()["counts"]["revisions"] == before+2


def test_attachment_provenance_and_queue_are_restored_in_full_bundle(setup, tmp_path):
    from gov_mcp.knowledge_backup import create_bundle, restore_bundle
    store, _, _, fetcher = setup
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    before = store.status()
    bundle = create_bundle(store, tmp_path / "bundle")
    restored = restore_bundle(bundle["directory"], tmp_path / "restore")
    after = KnowledgeStore(restored["restored_to"], read_only=True).status()
    assert before["attachments"] == after["attachments"]
    assert before["counts"] == after["counts"]


@pytest.mark.parametrize("change", [{"attachment_only": "true"}, {"seeds": [pdf_url()]}, {"attachment_parent_sources": [{}]}, {"attachment_max_pending": 1000}])
def test_collector_requires_explicit_bounded_unseeded_mode(setup, change):
    _, _, child, _ = setup
    with pytest.raises(ValueError):
        validate_config(dict(child, **change))

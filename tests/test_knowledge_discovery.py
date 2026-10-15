"""Synthetic protocol and queue fixtures; never evidence of a real site's permission or coverage."""

import copy
import time
from datetime import datetime, timezone

import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_discovery import parse_index, xml_root
from gov_mcp.knowledge_fetch import schedule, worker_once


@pytest.fixture
def discovery(tmp_path):
    store = KnowledgeStore.initialize(tmp_path / "discovery")
    config = {"id": "synthetic-discovery", "label": "纯模拟订阅", "enabled": True, "origin": "https://fixtures.invalid",
        "seeds": ["https://fixtures.invalid/feed"], "allowed_paths": ["/"], "interval_seconds": 3600, "delay_seconds": 5,
        "page_kind": "rss", "content_selector": {"id": "body"}, "discover_links": True,
        "discovery_keywords": ["身份证"], "discovery_max_links": 2, "discovery_max_pages": 3,
        "metadata": {"title": "待核定模拟文件", "region": "unknown", "matter": "unclassified", "document_type": "synthetic",
                     "effective_from": None, "effective_to": None, "usage_basis": "Self-authored engineering fixture", "scope_confirmed": False},
        "access_review": {"approved": True, "reviewer": "synthetic", "actor_kind": "synthetic", "usage_basis": "Self-authored fixture; no actual requests",
                          "checked_at": datetime.now(timezone.utc).isoformat()}}
    store.register(config)
    return store, config


class FixtureFetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    def fetch(self, config, url):
        self.requests.append(url)
        if url in self.responses:
            status, media, raw = self.responses[url]
        else:
            status, media = 200, "text/html"
            raw = ('<article id="body"><p>身份证受控模拟正文'+url+'</p></article>').encode()
        return status, {"content-type": media}, raw, url


def drain(store, fetcher, now=None):
    for _ in range(30):
        if not worker_once(store, fetcher, owner="synthetic-discovery", now=now):
            return
    pytest.fail("Bounded fixture unexpectedly failed to drain")


def rss(count=4):
    return ('<rss version="2.0"><channel>'+''.join('<item><title>身份证模拟文件'+str(i)+'</title><link>https://fixtures.invalid/docs/'+str(i)+'</link><pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item>' for i in range(count))+'</channel></rss>').encode()


def test_rss_link_dates_are_hints_and_budget_backlog_replays_after_304(discovery):
    store, config = discovery
    clock = time.time()
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/rss+xml", rss())})
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert store.status()["counts"]["documents"] == 2
    assert all(row["metadata"]["effective_from"] is None and row["metadata"]["region"] == "unknown" for row in store.status()["pending_review"])
    with store.db() as db:
        assert db.execute("SELECT limited FROM discovery_cycles ORDER BY id DESC LIMIT 1").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM discovery_edges").fetchone()[0] == 4
    fetcher.responses[config["seeds"][0]] = (304, "application/rss+xml", b"")
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    assert store.status()["counts"]["documents"] == 4
    assert fetcher.requests[4:6] == ["https://fixtures.invalid/docs/2", "https://fixtures.invalid/docs/3"]  # fresh discoveries precede old rechecks
    assert not store.status()["publications"]


def test_html_pagination_remains_index_and_cycles_do_not_loop(discovery):
    store, config = discovery
    config.update(page_kind="index", discovery_selector={"class": "policies"}, pagination_selector={"class": "pages"})
    store.register(config)
    root = ('<div class="policies"><a href="/docs/a">身份证模拟甲</a></div><div class="pages"><a href="/page2">下一页</a></div><footer><a href="/bad">身份证导航</a></footer>').encode()
    page2 = ('<div class="policies"><a href="/docs/b">身份证模拟乙</a></div><a rel="next" href="/feed">循环链接</a>').encode()
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "text/html", root), "https://fixtures.invalid/page2": (200, "text/html", page2)})
    schedule(store)
    drain(store, fetcher)
    assert store.status()["counts"]["documents"] == 2
    assert fetcher.requests.count(config["seeds"][0]) == 1
    assert "https://fixtures.invalid/bad" not in fetcher.requests
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM discovery_targets WHERE kind='index'").fetchone()[0] == 2


def test_atom_respects_xml_base_and_does_not_follow_foreign_origin(discovery):
    store, config = discovery
    config["page_kind"] = "atom"
    store.register(config)
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="/docs/"><entry><title>\xe8\xba\xab\xe4\xbb\xbd\xe8\xaf\x81 atom</title><id>synthetic-id</id><updated>2030-01-01T00:00:00Z</updated><link href="one"/></entry><link rel="next" href="https://foreign.invalid/next"/></feed>'''
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/atom+xml", raw)})
    schedule(store)
    drain(store, fetcher)
    assert fetcher.requests == [config["seeds"][0], "https://fixtures.invalid/docs/one"]
    assert store.status()["pending_review"][0]["metadata"]["effective_from"] is None


def test_sitemap_index_and_urlset_need_explicit_untitled_opt_in(discovery):
    store, config = discovery
    config.update(page_kind="sitemap", discover_untitled=True)
    store.register(config)
    root = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://fixtures.invalid/part.xml</loc></sitemap></sitemapindex>'
    child = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://fixtures.invalid/docs/one</loc><lastmod>2020-01-01</lastmod></url></urlset>'
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/xml", root), "https://fixtures.invalid/part.xml": (200, "text/xml", child)})
    schedule(store)
    drain(store, fetcher)
    assert store.status()["counts"]["documents"] == 1
    entries, _ = parse_index(child, "sitemap", config["seeds"][0], dict(config, discover_untitled=False))
    assert entries == []


@pytest.mark.parametrize("raw", [b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]><rss>&x;</rss>',
    b'<rss>'+b'<x>'*40+b'</x>'*40+b'</rss>', b'<rss><broken></rss>', b'\xff\xfe<\x00r\x00s\x00s\x00>\x00'])
def test_untrusted_xml_is_bounded_and_never_resolves_entities(raw):
    with pytest.raises((ValueError, UnicodeError)):
        xml_root(raw)


def test_active_discovery_cycle_is_not_reset_by_scheduler(discovery):
    store, config = discovery
    clock = time.time()
    schedule(store, now=clock)
    schedule(store, now=clock+3700)
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM discovery_cycles").fetchone()[0] == 1
        assert db.execute("SELECT attempts FROM jobs").fetchone()[0] == 0


def test_target_roles_cannot_be_silently_reconfigured(discovery):
    store, config = discovery
    schedule(store)
    changed = copy.deepcopy(config)
    changed["page_kind"] = "index"
    with pytest.raises(ValueError, match="target role"):
        store.register(changed)


def test_unclassified_watched_discovery_blocks_current_scope_until_decided(discovery):
    store, config = discovery
    # A separate fully synthetic document provides an already published baseline.
    from gov_mcp.knowledge import encode
    (store.root / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    core = copy.deepcopy(config)
    core.update(id="synthetic-core", page_kind="document", discover_links=False, seeds=["https://fixtures.invalid/core"])
    core["metadata"].update(region="controlled", matter="fixture", scope_confirmed=True, effective_from="2026-01-01")
    store.register(core)
    revision = store.ingest(core["id"], core["seeds"][0], b"synthetic-core", core["metadata"], [{"locator": "模拟条款", "category": "identity", "text": "身份证受控基准正文。"}])
    store.review(revision, "synthetic", True)
    version = store.publish(store.candidate("controlled", "fixture"), "synthetic", True)
    with store.db() as db:
        db.execute("UPDATE sites SET last_success=?,next_check=? WHERE id=?", (time.time(), time.time()+86400, core["id"]))
    assert store.search("身份证", "controlled", "fixture")["status"] == "OK"
    config["watch_scopes"] = [{"region": "controlled", "matter": "fixture"}]
    store.register(config)
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/rss+xml", rss(1))})
    schedule(store)
    drain(store, fetcher)
    pending = next(row for row in store.status()["pending_review"] if row["id"] != revision)
    assert store.search("身份证", "controlled", "fixture", version=version)["message_code"] == "UPDATE_PENDING_REVIEW"
    with pytest.raises(ValueError, match="classification"):
        store.candidate("controlled", "fixture")
    with pytest.raises(ValueError, match="Known region"):
        store.review(pending["id"], "synthetic", True)
    store.exclude(pending["id"], "synthetic", "受控测试：经核定为范围外的文件", True)
    assert store.search("身份证", "controlled", "fixture")["status"] == "OK"
    with store.db() as db:
        db.execute("UPDATE sites SET paused_reason='controlled denied monitor' WHERE id=?", (config["id"],))
    assert store.search("身份证", "controlled", "fixture")["message_code"] == "SOURCE_CHECK_REQUIRED"


@pytest.mark.parametrize("document_status", [200, 304])
def test_changed_feed_annotations_with_unchanged_body_need_new_review(discovery, document_status):
    store, config = discovery
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/rss+xml", rss(1))})
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    first = store.status()["pending_review"][0]
    assert first["metadata"]["source_observation"]["discovery_signal"]["hints"]["reported_pubdate"].startswith("Mon")
    fetcher.responses[config["seeds"][0]] = (200, "application/rss+xml", rss(1).replace(b'2001', b'2002'))
    if document_status == 304:
        fetcher.responses["https://fixtures.invalid/docs/0"] = (304, "text/html", b"")
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    updated = store.status()["pending_review"][0]
    assert updated["id"] != first["id"] and updated["metadata"]["effective_from"] is None
    assert updated["metadata"]["source_observation"]["discovery_signal"]["hints"]["reported_pubdate"].startswith("Mon, 01 Jan 2002")


def test_deferred_index_pages_are_revisited_next_cycle_without_exceeding_budget(discovery):
    store, config = discovery
    config["discovery_max_pages"] = 2
    store.register(config)
    def feed(next_url, doc):
        return ('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'+
            ('<atom:link rel="next" href="'+next_url+'"/>' if next_url else '')+
            ('<item><title>身份证模拟</title><link>'+doc+'</link></item>' if doc else '')+'</channel></rss>').encode()
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/rss+xml", feed("/page-a", "")),
        "https://fixtures.invalid/page-a": (200, "application/rss+xml", feed("/page-b", "/docs/a")),
        "https://fixtures.invalid/page-b": (200, "application/rss+xml", feed("", "/docs/b"))})
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert "https://fixtures.invalid/page-b" not in fetcher.requests
    assert store.status()["discovery"][0]["index_pages_scheduled"] == 2
    assert store.status()["discovery"][0]["limited"]
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    assert "https://fixtures.invalid/page-b" in fetcher.requests
    assert store.status()["counts"]["documents"] == 2
    assert store.status()["discovery"][0]["index_pages_scheduled"] == 2


def test_parser_truncation_remains_visible_when_index_returns_304(discovery, monkeypatch):
    import gov_mcp.knowledge_discovery as module
    monkeypatch.setattr(module, "MAX_ENTRIES", 1)
    store, config = discovery
    fetcher = FixtureFetcher({config["seeds"][0]: (200, "application/rss+xml", rss(2))})
    clock = time.time()
    schedule(store, now=clock)
    drain(store, fetcher, clock)
    assert store.status()["discovery"][0]["parser_truncated"]
    fetcher.responses[config["seeds"][0]] = (304, "application/rss+xml", b"")
    schedule(store, now=clock+3700)
    drain(store, fetcher, clock+3700)
    assert store.status()["discovery"][0]["parser_truncated"]


def test_html_response_cannot_be_mistaken_for_an_rss_document(discovery):
    store, config = discovery
    schedule(store)
    worker_once(store, FixtureFetcher({config["seeds"][0]: (200, "text/html", b'<html>login</html>')}))
    assert store.status()["counts"]["documents"] == 0
    assert "format" in store.status()["jobs"][0]["error"]


def test_fragment_links_deduplicate_and_unicode_paths_are_encoded_on_the_wire(discovery, monkeypatch):
    from gov_mcp.knowledge_fetch import Fetcher
    import gov_mcp.knowledge_fetch as module
    store, config = discovery
    entries, _ = parse_index('<a href="/docs/one#first">身份证模拟</a><a href="/docs/one#second">身份证模拟</a>'.encode(), "index", config["seeds"][0], config)
    assert len(entries) == 1 and entries[0]["url"].endswith("/docs/one")
    calls = []
    class Connection:
        def __init__(self, host, port, **kwargs):
            assert host == "fixtures.invalid" and port == 443
        def request(self, method, target, **kwargs):
            calls.append(target)
        def getresponse(self):
            class Response:
                length, status = 1, 200
                def read(self, _):
                    return b"x"
                def getheaders(self):
                    return [("Content-Type", "application/pdf")]
            return Response()
        def close(self):
            pass
    class Context:
        def wrap_socket(self, sock, **kwargs):
            assert kwargs["server_hostname"] == "fixtures.invalid"
            return sock
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(module.socket, "create_connection", lambda *args, **kwargs: object())
    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(module.ssl, "create_default_context", Context)
    Fetcher(store)._request("https://fixtures.invalid/docs/身份证.pdf?分类=通知", {})
    assert calls == ["/docs/%E8%BA%AB%E4%BB%BD%E8%AF%81.pdf?%E5%88%86%E7%B1%BB=%E9%80%9A%E7%9F%A5"]

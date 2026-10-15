"""Bounded HTML/RSS/Atom/sitemap discovery, with durable target roles and cached index edges."""

import email.utils
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from gov_mcp.knowledge import encode


INDEX_KINDS = frozenset({"index", "rss", "atom", "sitemap"})
XML_TYPES = frozenset({"application/xml", "text/xml", "application/rss+xml", "application/atom+xml"})
MAX_ENTRIES = 2000
VOID = frozenset("br img input meta link hr source wbr area base embed param col track".split())


def validate_discovery(config):
    kind = config.get("page_kind", "document")
    if kind not in INDEX_KINDS | {"document"}:
        raise ValueError("Supported document/index/RSS/Atom/sitemap kind required")
    for name, default, maximum in (("discovery_max_links", 25, 100), ("discovery_max_pages", 10, 25), ("discovery_max_depth", 10, 20)):
        value = config.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Bounded discovery page, depth and new-document limits required")
    keywords = config.get("discovery_keywords", [])
    if not isinstance(keywords, list) or len(keywords) > 20 or any(not isinstance(word, str) or not word.strip() or len(word) > 100 for word in keywords):
        raise ValueError("Bounded discovery keyword list required")
    if type(config.get("discover_untitled", False)) is not bool:
        raise ValueError("Explicit boolean untitled discovery policy required")
    for key in ("discovery_upgrade_same_host_https", "discovery_full_title"):
        if type(config.get(key, False)) is not bool:
            raise ValueError("Explicit boolean discovery transport/title policy required")
    owners = config.get("discovery_document_owners", {})
    if (not isinstance(owners, dict) or len(owners) > 100
            or any(not isinstance(url, str) or len(url) > 2048 or not isinstance(owner, str)
                   or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", owner) or owner == config["id"] for url, owner in owners.items())):
        raise ValueError("Explicit bounded discovery document owners required")
    from gov_mcp.knowledge_fetch import allowed_url
    for url in owners:
        allowed_url(config, url)
    numbered = config.get("discovery_numbered_pagination")
    if numbered is not None:
        if (not isinstance(numbered, dict) or set(numbered) != {"id", "total_attribute", "size_attribute", "url_pattern"}
                or any(not isinstance(numbered[key], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", numbered[key])
                       for key in ("id", "total_attribute", "size_attribute"))
                or not isinstance(numbered["url_pattern"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]*\{page\}[A-Za-z0-9_.-]*", numbered["url_pattern"])):
            raise ValueError("Reviewed relative numbered-pagination pattern required")
    watches = config.get("watch_scopes", [])
    if not isinstance(watches, list) or len(watches) > 20 or any(not isinstance(scope, dict) or set(scope) != {"region", "matter"} or any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in scope.values()) for scope in watches):
        raise ValueError("Explicit bounded potential-impact watch scopes required")
    for key in ("discovery_selector", "pagination_selector"):
        selector = config.get(key)
        if selector is not None and (not isinstance(selector, dict) or len(selector) != 1 or next(iter(selector)) not in ("id", "class") or not isinstance(next(iter(selector.values())), str) or not next(iter(selector.values())).strip()):
            raise ValueError("Explicit discovery id/class selector required")


def configured_kind(config, url):
    if url in config.get("index_urls", []):
        return "index"
    return config.get("page_kind", "document") if url in config["seeds"] else "document"


def target_kind(db, config, url):
    row = db.execute("SELECT kind FROM discovery_targets WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
    return row[0] if row else configured_kind(config, url)


def watches_unclassified(config, metadata, region, matter):
    unknown = (metadata["region"] in ("unknown", "unclassified", "") or metadata["matter"] in ("unknown", "unclassified", "") or metadata.get("scope_confirmed") is False)
    return unknown and {"region": region, "matter": matter} in config.get("watch_scopes", [])


def watched_source_ids(db, region, matter):
    target = {"region": region, "matter": matter}
    return {row["id"] for row in db.execute("SELECT id,config FROM sites") if target in json.loads(row["config"]).get("watch_scopes", [])}


class HTMLDiscovery(HTMLParser):
    def __init__(self, base, selector=None, pagination=None, keywords=None, untitled=False, numbered=None, full_title=False):
        super().__init__()
        self.base, self.selector, self.pagination = base, selector, pagination
        self.stack, self.anchor, self.entries = [], None, []
        self.keywords, self.untitled, self.truncated = keywords or [], untitled, False
        self.numbered, self.numbered_counts, self.full_title = numbered, [], full_title
        self.selector_found = selector is None

    @staticmethod
    def matches(attrs, selector):
        return bool(selector and ("id" in selector and attrs.get("id") == selector["id"] or "class" in selector and selector["class"] in (attrs.get("class") or "").split()))

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        active = self.selector is None or any(item[1] for item in self.stack) or self.matches(attrs, self.selector)
        pager = any(item[2] for item in self.stack) or self.matches(attrs, self.pagination)
        hidden = tag in ("script", "style", "footer", "noscript") or any(item[3] for item in self.stack)
        if self.matches(attrs, self.selector) and not hidden:
            self.selector_found = True
        if self.numbered and attrs.get("id") == self.numbered["id"] and not hidden:
            total, size = (attrs.get(self.numbered[key], "") for key in ("total_attribute", "size_attribute"))
            if not re.fullmatch(r"[0-9]{1,7}", total or "") or not re.fullmatch(r"[0-9]{1,5}", size or "") or int(size) < 1:
                raise ValueError("Invalid declared pagination totals")
            self.numbered_counts.append((int(total)+int(size)-1)//int(size))
        if tag == "base" and attrs.get("href"):
            self.base = urljoin(self.base, attrs["href"])
        if tag not in VOID:
            self.stack.append((tag, active, pager, hidden))
            if len(self.stack) > 128:
                raise ValueError("Discovery HTML nesting exceeds bounds")
        if tag == "a" and attrs.get("href") and not hidden:
            self.anchor = [attrs["href"], [], attrs.get("title", ""), active, pager, "next" in (attrs.get("rel") or "").split()]

    def handle_data(self, text):
        if self.anchor:
            self.anchor[1].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            href, pieces, fallback, active, pager, rel_next = self.anchor
            title = re.sub(r"\s+", " ", (fallback if self.full_title and fallback else "".join(pieces) or fallback)).strip()
            is_next = rel_next or pager and title.lower() in ("下一页", "下页", "next", "next page", ">", "›", "»")
            matches = (title and (not self.keywords or any(word in title for word in self.keywords)) or not title and self.untitled)
            if is_next or active and not pager and matches:
                if len(self.entries) < MAX_ENTRIES:
                    self.entries.append({"url": urljoin(self.base, href), "title": title[:300], "kind": "index" if is_next else "document", "hints": {}})
                else:
                    self.truncated = True
            self.anchor = None
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def xml_root(raw):
    if len(raw) > 5*1024*1024:
        raise ValueError("Oversized discovery XML")
    text = raw.decode("utf-8-sig", errors="strict")
    if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("DTD/entity declarations and non-UTF8 XML are not accepted")
    parser, depth, nodes, root = ET.XMLPullParser(events=("start", "end")), 0, 0, None
    try:
        for offset in range(0, len(text), 8192):
            parser.feed(text[offset:offset+8192])
            for event, element in parser.read_events():
                if event == "start":
                    depth, nodes = depth+1, nodes+1
                    root = root if root is not None else element
                    if depth > 32 or nodes > 30000:
                        raise ValueError("Discovery XML structure exceeds bounds")
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError as error:
        raise ValueError("Malformed discovery XML") from error
    if root is None:
        raise ValueError("Empty discovery XML")
    return root


def child_text(element, name):
    found = next((child for child in element if child.tag.rsplit("}", 1)[-1] == name), None)
    return "".join(found.itertext()).strip() if found is not None else ""


def parse_index(raw, kind, base, config):
    parser_truncated = False
    if kind == "index":
        parser = HTMLDiscovery(base, config.get("discovery_selector"), config.get("pagination_selector"), config.get("discovery_keywords"), config.get("discover_untitled", False),
                               config.get("discovery_numbered_pagination"), config.get("discovery_full_title", False))
        parser.feed(raw.decode("utf-8", errors="strict"))
        if not parser.selector_found:
            raise ValueError("Configured discovery list selector missing")
        entries = parser.entries
        parser_truncated = parser.truncated
        if parser.numbered:
            if len(parser.numbered_counts) != 1:
                raise ValueError("Unique declared pagination marker required")
            count = parser.numbered_counts[0]
            entries.extend({"url": urljoin(base, parser.numbered["url_pattern"].format(page=page)), "title": "", "kind": "index", "hints": {}}
                           for page in range(1, min(count, MAX_ENTRIES)))
            parser_truncated |= count > MAX_ENTRIES
    else:
        root, entries = xml_root(raw), []
        local = root.tag.rsplit("}", 1)[-1]
        xmlbase = "{http://www.w3.org/XML/1998/namespace}base"
        root_base = urljoin(base, root.attrib.get(xmlbase, ""))
        if kind == "rss" and local == "rss":
            channel = next((child for child in root if child.tag.rsplit("}", 1)[-1] == "channel"), None)
            if channel is None:
                raise ValueError("RSS channel missing")
            for item in channel:
                tag = item.tag.rsplit("}", 1)[-1]
                if tag == "item":
                    link = child_text(item, "link")
                    if link:
                        entries.append({"url": urljoin(root_base, link), "title": child_text(item, "title")[:300], "kind": "document",
                            "hints": {"feed_guid": child_text(item, "guid"), "reported_pubdate": child_text(item, "pubDate")}})
                elif tag == "link" and item.get("rel") == "next" and item.get("href"):
                    entries.append({"url": urljoin(root_base, item.get("href")), "title": "", "kind": "rss", "hints": {}})
        elif kind == "atom" and root.tag == "{http://www.w3.org/2005/Atom}feed":
            for item in root:
                tag = item.tag.rsplit("}", 1)[-1]
                item_base = urljoin(root_base, item.get(xmlbase, ""))
                if tag == "link" and item.get("rel") == "next" and item.get("href"):
                    entries.append({"url": urljoin(item_base, item.get("href")), "title": "", "kind": "atom", "hints": {}})
                elif tag == "entry":
                    link = next((link for link in item if link.tag.rsplit("}", 1)[-1] == "link" and link.get("rel", "alternate") == "alternate" and link.get("href")), None)
                    if link is not None:
                        entries.append({"url": urljoin(urljoin(item_base, link.get(xmlbase, "")), link.get("href")),
                            "title": child_text(item, "title")[:300], "kind": "document", "hints": {"feed_id": child_text(item, "id"),
                            "reported_published": child_text(item, "published"), "reported_updated": child_text(item, "updated")}})
        elif kind == "sitemap" and root.tag in ("{http://www.sitemaps.org/schemas/sitemap/0.9}urlset", "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemapindex"):
            for item in root:
                loc = child_text(item, "loc")
                if loc:
                    entries.append({"url": urljoin(base, loc), "title": "", "kind": "sitemap" if local == "sitemapindex" else "document",
                                    "hints": {"reported_lastmod": child_text(item, "lastmod")}})
        else:
            raise ValueError("Discovery XML does not match its configured format")
    if any(len(entry["url"]) > 2048 or len(encode(entry["hints"])) > 4096 for entry in entries):
        raise ValueError("Oversized discovery link or hint")
    keywords = config.get("discovery_keywords", [])
    selected, seen = [], set()
    for entry in entries:
        entry["url"] = urldefrag(entry["url"])[0]
        if config.get("discovery_upgrade_same_host_https"):
            target, origin = urlsplit(entry["url"]), urlsplit(config["origin"])
            if (origin.scheme == "https" and target.scheme == "http" and target.hostname == origin.hostname
                    and target.port in (None, 80) and not target.username and not target.password):
                entry["hints"]["original_link_url"] = entry["url"]
                entry["url"] = urlunsplit(("https", origin.netloc, target.path, target.query, ""))
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        if entry["kind"] == "document":
            if not entry["title"] and not config.get("discover_untitled", False):
                continue
            if entry["title"] and keywords and not any(word in entry["title"] for word in keywords):
                continue
        selected.append(entry)
    selected.sort(key=entry_recency, reverse=True)
    return selected[:MAX_ENTRIES], parser_truncated or len(selected) > MAX_ENTRIES


def entry_recency(entry):
    """Reported feed dates only prioritize discovery work; they never set policy dates."""
    for key in ("reported_updated", "reported_pubdate", "reported_published", "reported_lastmod"):
        value = entry["hints"].get(key)
        if not value:
            continue
        try:
            parsed = email.utils.parsedate_to_datetime(value) if key == "reported_pubdate" else datetime.fromisoformat(value.replace("Z", "+00:00"))
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            timestamp = parsed.timestamp()
            if timestamp <= datetime.now(timezone.utc).timestamp()+86400:
                return 1, timestamp
        except (ValueError, TypeError, OverflowError):
            pass
    return 0, 0


def enqueue_cycle(db, config, now):
    """Do not overlap cycles; cached child indexes rotate toward least-recently checked pages."""
    if db.execute("SELECT 1 FROM jobs WHERE site_id=? AND state IN ('queued','running') LIMIT 1", (config["id"],)).fetchone():
        return False
    cursor = db.execute("INSERT INTO discovery_cycles(site_id,started_at) VALUES(?,?)", (config["id"], now))
    cycle = cursor.lastrowid
    roots = set(config["seeds"]) | set(config.get("index_urls", []))
    for url in sorted(roots):
        queue_target(db, config, cycle, {"url": url, "kind": configured_kind(config, url), "title": "", "hints": {}}, 0, now, root=True)
    # Recheck known documents; an omitted link never means the rule was revoked.
    known = db.execute("SELECT j.url FROM jobs j LEFT JOIN discovery_targets t ON t.site_id=j.site_id AND t.url=j.url WHERE j.site_id=? AND COALESCE(t.kind,'document')='document'", (config["id"],)).fetchall()
    for row in known:
        if row[0] not in roots:
            queue_target(db, config, cycle, {"url": row[0], "kind": "document", "title": "", "hints": {}}, 0, now)
    cached_pages = db.execute("SELECT DISTINCT e.target_url,e.kind FROM discovery_edges e LEFT JOIN resources r ON r.site_id=e.site_id AND r.url=e.target_url WHERE e.site_id=? AND e.kind!='document' ORDER BY COALESCE(r.checked_at,0),e.target_url", (config["id"],)).fetchall()
    for page in cached_pages:
        queue_target(db, config, cycle, {"url": page[0], "kind": page[1], "title": "", "hints": {}}, 1, now)
    return True


def queue_target(db, config, cycle, entry, depth, now, root=False):
    from gov_mcp.knowledge_fetch import AccessStopped, allowed_url
    try:
        url = allowed_url(config, entry["url"])
    except AccessStopped:
        return "outside_policy"
    if db.execute("SELECT 1 FROM discovery_visits WHERE cycle_id=? AND url=?", (cycle, url)).fetchone():
        return "seen"
    existing = db.execute("SELECT kind FROM discovery_targets WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
    if existing and existing[0] != entry["kind"]:
        raise ValueError("Discovery target role changed; review source configuration")
    old_job = db.execute("SELECT id FROM jobs WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
    is_new = entry["kind"] == "document" and not old_job and not root
    pages = db.execute("SELECT COUNT(*) FROM discovery_visits WHERE cycle_id=? AND kind!='document'", (cycle,)).fetchone()[0]
    documents = db.execute("SELECT COUNT(*) FROM discovery_visits WHERE cycle_id=? AND is_new=1", (cycle,)).fetchone()[0]
    if depth > config.get("discovery_max_depth", 10) or entry["kind"] != "document" and pages >= config.get("discovery_max_pages", 10) or is_new and documents >= config.get("discovery_max_links", 25):
        db.execute("UPDATE discovery_cycles SET limited=1 WHERE id=?", (cycle,))
        return "deferred_by_limit"
    db.execute("INSERT INTO discovery_visits VALUES(?,?,?,?,?)", (cycle, url, entry["kind"], depth, int(is_new)))
    db.execute("INSERT INTO discovery_targets VALUES(?,?,?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET cycle_id=excluded.cycle_id,depth=excluded.depth", (config["id"], url, entry["kind"], cycle, depth))
    db.execute("INSERT INTO jobs(site_id,url,due) VALUES(?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET state='queued',attempts=0,due=excluded.due,error=NULL WHERE jobs.state IN ('done','failed')", (config["id"], url, now))
    return "new_document" if is_new else "queued"


def record_index(store, config, url, kind, raw, final_url, now):
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        target = db.execute("SELECT cycle_id,depth FROM discovery_targets WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
        if not target:
            raise ValueError("Discovery index requires an active scheduled cycle")
        cycle, depth = target
        if raw is None:
            entries = [{"url": row["target_url"], "kind": row["kind"], "title": row["title"], "hints": json.loads(row["hints"])} for row in db.execute("SELECT * FROM discovery_edges WHERE site_id=? AND parent_url=? ORDER BY ordinal", (config["id"], url))]
            previous = db.execute("SELECT parser_truncated FROM discovery_index_state WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
            truncated = bool(previous and previous[0])
            previous_audit = db.execute("SELECT data FROM audit WHERE action='discovery_index_checked' AND json_extract(data,'$.site')=? AND json_extract(data,'$.url')=? ORDER BY id DESC LIMIT 1", (config["id"], url)).fetchone()
            rejected = json.loads(previous_audit[0]).get("policy_rejected_entries", 0) if previous_audit else 0
        else:
            entries, truncated = parse_index(raw, kind, final_url, config)
            from gov_mcp.knowledge_fetch import AccessStopped, allowed_url
            accepted = []
            rejected = 0
            for entry in entries:
                try:
                    entry["url"] = allowed_url(config, entry["url"])
                except AccessStopped:
                    rejected += 1
                    continue
                accepted.append(entry)
            entries = accepted
            db.execute("DELETE FROM discovery_edges WHERE site_id=? AND parent_url=?", (config["id"], url))
            db.executemany("INSERT INTO discovery_edges VALUES(?,?,?,?,?,?,?,?)", [(config["id"], url, entry["url"], entry["kind"], entry["title"], encode(entry["hints"]), ordinal, now) for ordinal, entry in enumerate(entries)])
        counts = {}
        for entry in entries:
            outcome = (delegate_document(store, db, config, cycle, entry, depth+1, url, kind, now)
                       or queue_target(db, config, cycle, entry, depth+1, now))
            counts[outcome] = counts.get(outcome, 0)+1
            if outcome == "new_document":
                store.log(db, "document_link_discovered", {"site": config["id"], "url": entry["url"], "title": entry["title"], "discovered_from": url, "discovery_format": kind, "hints": entry["hints"]})
        if truncated:
            db.execute("UPDATE discovery_cycles SET limited=1 WHERE id=?", (cycle,))
        db.execute("INSERT INTO discovery_index_state VALUES(?,?,?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET parser_truncated=excluded.parser_truncated,entry_count=excluded.entry_count,checked_at=excluded.checked_at", (config["id"], url, int(truncated), len(entries), now))
        store.log(db, "discovery_index_checked", {"site": config["id"], "url": url, "format": kind, "cycle": cycle,
            "cached_edges_replayed": raw is None, "matching_entries": len(entries), "parser_truncated": truncated, "outcomes": counts,
            "policy_rejected_entries": rejected,
            "dates_are_hints_not_policy_effectivity": True})


def delegate_document(store, db, config, cycle, entry, depth, parent, kind, now):
    """Reuse an explicitly named monitor, with the directory signal and recheck retained."""
    owner = config.get("discovery_document_owners", {}).get(entry["url"]) if entry["kind"] == "document" else None
    if not owner:
        return None
    if db.execute("SELECT 1 FROM discovery_visits WHERE cycle_id=? AND url=?", (cycle, entry["url"])).fetchone():
        return "seen"
    row = db.execute("SELECT config FROM sites WHERE id=?", (owner,)).fetchone()
    owner_config = json.loads(row[0]) if row else {}
    from gov_mcp.knowledge_fetch import allowed_url
    if (not owner_config.get("enabled") or entry["url"] not in owner_config.get("seeds", [])
            or not db.execute("SELECT 1 FROM documents WHERE site_id=? AND url=?", (owner, entry["url"])).fetchone()
            or any(scope not in owner_config.get("watch_scopes", []) for scope in config.get("watch_scopes", []))):
        raise ValueError("Declared document owner must be an enabled archived monitor of the watched scopes")
    allowed_url(owner_config, entry["url"])
    signal = {"site": owner, "url": entry["url"], "title": entry["title"], "discovered_from": parent,
              "discovery_format": kind, "hints": dict(entry["hints"], delegated_from_source=config["id"])}
    previous = db.execute("SELECT data FROM audit WHERE action='document_link_discovered' AND json_extract(data,'$.site')=? AND json_extract(data,'$.url')=? ORDER BY id DESC LIMIT 1", (owner, entry["url"])).fetchone()
    if not previous or json.loads(previous[0]) != signal:
        store.log(db, "document_link_discovered", signal)
        # Do not keep serving a current answer while a changed directory signal
        # awaits the owner's body/304 check and immutable pending revision.
        db.execute("UPDATE sites SET last_success=NULL WHERE id=?", (owner,))
    db.execute("INSERT OR IGNORE INTO discovery_visits VALUES(?,?,?,?,0)", (cycle, entry["url"], "document", depth))
    db.execute("INSERT INTO jobs(site_id,url,due) VALUES(?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET state='queued',attempts=0,due=excluded.due,error=NULL WHERE jobs.state IN ('done','failed')", (owner, entry["url"], now))
    store.log(db, "discovery_document_delegated", {"site": config["id"], "url": entry["url"], "owner": owner, "cycle": cycle})
    return "delegated_document"


def document_discovery_signal(db, config, url):
    edge = db.execute("SELECT e.*,t.kind AS discovery_format FROM discovery_edges e LEFT JOIN discovery_targets t ON t.site_id=e.site_id AND t.url=e.parent_url WHERE e.site_id=? AND e.target_url=? ORDER BY e.observed_at DESC,e.ordinal LIMIT 1", (config["id"], url)).fetchone()
    if edge:
        return {"title": edge["title"], "discovered_from": edge["parent_url"], "discovery_format": edge["discovery_format"], "hints": json.loads(edge["hints"])}
    anchor = db.execute("SELECT data FROM audit WHERE action='document_link_discovered' AND json_extract(data,'$.site')=? AND json_extract(data,'$.url')=? ORDER BY id DESC LIMIT 1", (config["id"], url)).fetchone()
    return json.loads(anchor[0]) if anchor else None


def refresh_unchanged_document(store, config, url):
    """A document's HTTP 304 does not erase a changed title/date in its feed."""
    with store.db() as db:
        latest = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (config["id"], url)).fetchone()
        if not latest:
            raise ValueError("Document 304 without an archived representation")
        edge = document_discovery_signal(db, config, url)
        if not edge:
            return
        signal = {key: edge.get(key) for key in ("title", "hints", "discovery_format")}
        previous = json.loads(latest["metadata"])
        observation = dict(previous.get("source_observation", {}))
        if observation.get("discovery_signal") == signal:
            return
        store._verify(db, latest["id"], require_review=False)
        _, chunks = store._content(db, latest["id"])
    observation["discovery_signal"] = signal
    metadata = dict(config["metadata"], effective_from=None, effective_to=None, published_at=None,
                    source_observation=observation, title=edge["title"] or previous["title"],
                    title_source="source_"+str(edge["discovery_format"])+"_entry_pending_document_verification",
                    discovered_from=edge["discovered_from"])
    original = store.root / "originals" / latest["sha256"][:2] / latest["sha256"]
    revision = store.ingest(config["id"], url, original.read_bytes(), metadata, chunks, preserve_metadata_if_unchanged=True)
    store.propose_classification(revision)


def discovery_status(db):
    result = []
    for row in db.execute("SELECT * FROM discovery_cycles WHERE id IN (SELECT MAX(id) FROM discovery_cycles GROUP BY site_id) ORDER BY site_id"):
        visits = db.execute("SELECT COUNT(*),SUM(kind!='document'),SUM(is_new) FROM discovery_visits WHERE cycle_id=?", (row["id"],)).fetchone()
        backlog = db.execute("SELECT COUNT(DISTINCT e.target_url) FROM discovery_edges e LEFT JOIN discovery_visits v ON v.cycle_id=? AND v.url=e.target_url WHERE e.site_id=? AND v.url IS NULL", (row["id"], row["site_id"])).fetchone()[0]
        truncated = bool(db.execute("SELECT 1 FROM discovery_index_state WHERE site_id=? AND parser_truncated=1 LIMIT 1", (row["site_id"],)).fetchone())
        result.append({"source_id": row["site_id"], "cycle_id": row["id"], "started_at": row["started_at"],
            "limited": bool(row["limited"]), "index_pages_scheduled": visits[1] or 0,
            "new_document_jobs": visits[2] or 0, "unvisited_cached_targets": backlog,
            "parser_truncated": truncated,
            "active_jobs": db.execute("SELECT COUNT(*) FROM jobs WHERE site_id=? AND state IN ('queued','running')", (row["site_id"],)).fetchone()[0],
            "failed_jobs": db.execute("SELECT COUNT(*) FROM jobs WHERE site_id=? AND state='failed'", (row["site_id"],)).fetchone()[0],
            "coverage_note": "Bounded declared entry points and cached links; not proof of a site's complete document inventory"})
    return result
